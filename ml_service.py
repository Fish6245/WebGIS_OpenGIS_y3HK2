from pathlib import Path
import json
import traceback

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from catboost import CatBoostRegressor

MODEL_DIR = Path("./model/artifacts")
EXCEL_FILE = Path("./public/data/HCM.xlsx")
SHEET_NAME = "road_giadat_tphcm"
TARGETS = ["GiaDat2019", "GiaDat2025"]

app = FastAPI(title="Land Price ML Service")


class PredictRequest(BaseModel):
    Latitude: float | None = None
    Longitude: float | None = None

    TenDuong: str | None = None
    Phuong: str | None = None
    QuanHuyen: str | None = None

    ThanhPho: str | None = None
    TinhThanh: str | None = None
    QuocGia: str | None = None

    nearbyRoads: list = Field(default_factory=list)
    nearbyPlaces: list = Field(default_factory=list)


models = {}
feature_lists = {}
metas = {}
source_df = None


def load_json_file(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


for target in TARGETS:
    model = CatBoostRegressor()
    model.load_model(str(MODEL_DIR / f"catboost_spatial_{target}.cbm"))
    models[target] = model
    feature_lists[target] = load_json_file(MODEL_DIR / f"model_{target}_features.json")
    metas[target] = load_json_file(MODEL_DIR / f"catboost_spatial_{target}_meta.json")


def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())
    return s.strip()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def sync_pair(df: pd.DataFrame, left: str, right: str) -> pd.DataFrame:
    if left in df.columns and right in df.columns:
        df[left] = df[left].where(df[left].notna(), df[right])
        df[right] = df[right].where(df[right].notna(), df[left])
    elif left in df.columns and right not in df.columns:
        df[right] = df[left]
    elif right in df.columns and left not in df.columns:
        df[left] = df[right]
    return df


def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # raw aliases: ưu tiên cặp có dấu _
    df = sync_pair(df, "TenDuong", "TenDuong_")
    df = sync_pair(df, "QuanHuyen", "QuanHuyen_")
    df = sync_pair(df, "Phuong", "Phuong_")
    df = sync_pair(df, "ThanhPho", "ThanhPho_")
    df = sync_pair(df, "TinhThanh", "TinhThanh_")
    df = sync_pair(df, "QuocGia", "QuocGia_")

    if "X" not in df.columns and "Longitude" in df.columns:
        df["X"] = df["Longitude"]
    if "Y" not in df.columns and "Latitude" in df.columns:
        df["Y"] = df["Latitude"]

    if "road_count" not in df.columns:
        df["road_count"] = np.nan
    if "place_count" not in df.columns:
        df["place_count"] = np.nan
    if "nearest_road_distance" not in df.columns:
        df["nearest_road_distance"] = np.nan
    if "nearest_place_distance" not in df.columns:
        df["nearest_place_distance"] = np.nan

    for col in ["ThanhPho", "TinhThanh", "QuocGia", "ThanhPho_", "TinhThanh_", "QuocGia_"]:
        if col not in df.columns:
            df[col] = ""

    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = canonicalize_columns(df).copy()

    text_like_cols = [
        "TenDuong",
        "TenDuong_",
        "QuanHuyen",
        "QuanHuyen_",
        "Phuong",
        "Phuong_",
        "ThanhPho",
        "ThanhPho_",
        "TinhThanh",
        "TinhThanh_",
        "QuocGia",
        "QuocGia_",
    ]

    for col in text_like_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").map(normalize_text)

    for col in [
        "X",
        "Y",
        "road_count",
        "place_count",
        "nearest_road_distance",
        "nearest_place_distance",
    ]:
        if col in df.columns:
            df[col] = safe_numeric(df[col])

    def _col(name_primary: str, name_fallback: str | None = None) -> pd.Series:
        if name_primary in df.columns:
            return df[name_primary].fillna("").astype(str)
        if name_fallback and name_fallback in df.columns:
            return df[name_fallback].fillna("").astype(str)
        return pd.Series([""] * len(df), index=df.index)

    road_name = _col("TenDuong_", "TenDuong")
    district_name = _col("QuanHuyen_", "QuanHuyen")
    ward_name = _col("Phuong_", "Phuong")
    city_name = _col("ThanhPho_", "ThanhPho")
    province_name = _col("TinhThanh_", "TinhThanh")

    df["road_key"] = (
        road_name + "|" + district_name + "|" + ward_name
    ).map(normalize_text)

    df["road_district_key"] = (
        road_name + "|" + district_name
    ).map(normalize_text)

    df["road_ward_key"] = (
        road_name + "|" + ward_name
    ).map(normalize_text)

    df["district_ward_key"] = (
        district_name + "|" + ward_name
    ).map(normalize_text)

    df["road_full_key"] = (
        road_name + "|"
        + district_name + "|"
        + ward_name + "|"
        + city_name + "|"
        + province_name
    ).map(normalize_text)

    def first_token(s: str) -> str:
        s = normalize_text(s)
        return s.split()[0] if s else ""

    def last_token(s: str) -> str:
        s = normalize_text(s)
        return s.split()[-1] if s else ""

    df["TenDuong_prefix"] = road_name.map(first_token)
    df["TenDuong_suffix"] = road_name.map(last_token)

    df["road_name_len"] = road_name.astype(str).str.len()
    df["road_name_word_count"] = road_name.astype(str).str.split().str.len()

    df["district_len"] = district_name.astype(str).str.len()
    df["district_word_count"] = district_name.astype(str).str.split().str.len()

    df["ward_len"] = ward_name.astype(str).str.len()
    df["ward_word_count"] = ward_name.astype(str).str.split().str.len()

    df["road_key_len"] = df["road_key"].astype(str).str.len()
    df["road_key_word_count"] = df["road_key"].astype(str).str.split().str.len()

    if "X" in df.columns:
        df["x_round_3"] = df["X"].round(3)
    else:
        df["x_round_3"] = np.nan

    if "Y" in df.columns:
        df["y_round_3"] = df["Y"].round(3)
    else:
        df["y_round_3"] = np.nan

    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) == "category":
            df[col] = df[col].fillna("").astype(str)

    return df


def create_runtime_features(payload: dict) -> dict:
    roads = payload.get("nearbyRoads") or []
    places = payload.get("nearbyPlaces") or []

    row = {
        "TenDuong": payload.get("TenDuong") or "",
        "TenDuong_": payload.get("TenDuong") or "",
        "QuanHuyen": payload.get("QuanHuyen") or "",
        "QuanHuyen_": payload.get("QuanHuyen") or "",
        "Phuong": payload.get("Phuong") or "",
        "Phuong_": payload.get("Phuong") or "",
        "ThanhPho": payload.get("ThanhPho") or "",
        "ThanhPho_": payload.get("ThanhPho") or "",
        "TinhThanh": payload.get("TinhThanh") or "",
        "TinhThanh_": payload.get("TinhThanh") or "",
        "QuocGia": payload.get("QuocGia") or "VN",
        "QuocGia_": payload.get("QuocGia") or "VN",
        "X": payload.get("Longitude"),
        "Y": payload.get("Latitude"),
        "road_count": len(roads),
        "place_count": len(places),
        "nearest_road_distance": np.nan,
        "nearest_place_distance": np.nan,
    }

    if roads:
        row["nearest_road_distance"] = min(
            float(r.get("distance_m", 999999)) for r in roads
        )

    if places:
        row["nearest_place_distance"] = min(
            float(p.get("distance_m", 999999)) for p in places
        )

    return row


def prepare_model_input(df: pd.DataFrame, feature_cols: list[str], cat_cols: list[str]) -> pd.DataFrame:
    X = df.reindex(columns=feature_cols).copy()
    cat_set = set(cat_cols or [])

    for col in X.columns:
        if col in cat_set:
            X[col] = X[col].apply(lambda v: "" if pd.isna(v) else normalize_text(v)).astype(str)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce")  # giữ NaN như manual

    return X


def load_source_data():
    global source_df
    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME)
        df = canonicalize_columns(df)

        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].fillna("").astype(str).str.strip()

        source_df = df
        print(f"Loaded source data: {len(source_df)} rows")
        print(f"Source file: {EXCEL_FILE}")
        print(f"Source columns: {list(source_df.columns)}")
    except Exception as e:
        source_df = None
        print(f"Cannot load source Excel: {e}")


def find_best_row(payload: dict) -> pd.Series | None:
    if source_df is None:
        return None

    ten_duong = normalize_text(payload.get("TenDuong") or "")
    quan_huyen = normalize_text(payload.get("QuanHuyen") or "")

    if not ten_duong or not quan_huyen:
        return None

    df = source_df.copy()
    df = canonicalize_columns(df)

    road_col = "TenDuong_" if "TenDuong_" in df.columns else "TenDuong"
    district_col = "QuanHuyen_" if "QuanHuyen_" in df.columns else "QuanHuyen"

    df[road_col] = df[road_col].fillna("").astype(str).map(normalize_text)
    df[district_col] = df[district_col].fillna("").astype(str).map(normalize_text)

    mask = (
        df[road_col].str.lower().str.strip() == ten_duong.lower().strip()
    ) & (
        df[district_col].str.lower().str.strip() == quan_huyen.lower().strip()
    )

    matches = df[mask]

    print(
        f"[MATCH] TenDuong={repr(ten_duong)} | QuanHuyen={repr(quan_huyen)} -> {len(matches)} rows"
    )

    if len(matches) == 0:
        return None

    return matches.iloc[0].copy()


@app.get("/health")
def health():
    return {"ok": True, "message": "ML service is running"}


@app.post("/predict")
def predict(req: PredictRequest):
    try:
        payload = req.model_dump()

        source_row = find_best_row(payload)
        if source_row is not None:
            matched = True
            print("MATCH FOUND")
            print(source_row["GiaDat2019"])
            print(source_row["GiaDat2025"])
            result = {
                "GiaDat2019": round(float(source_row["GiaDat2019"]), 0),
                "GiaDat2025": round(float(source_row["GiaDat2025"]), 0),
            }

        else:
            matched = False

            row_df = pd.DataFrame([create_runtime_features(payload)])
            row_df = build_features(row_df)

            result = {}

            for target in TARGETS:
                feature_cols = feature_lists[target]
                cat_cols = metas[target].get("cat_cols", [])

                X = prepare_model_input(row_df, feature_cols, cat_cols)

                pred = models[target].predict(X)[0]

                if metas[target].get("use_log_target", False):
                    pred = np.expm1(pred)

                result[target] = round(float(pred), 0)

        # ==================================================
        # RESPONSE
        # ==================================================
        response = {
            "ok": True,
            "matched_source": matched,
            "source_type": (
                "file_match"
                if matched
                else "model_prediction"
            ),
            "prediction": result,
            "GiaDat2019": result.get("GiaDat2019"),
            "GiaDat2025": result.get("GiaDat2025"),

            "matched_info": {
                "TenDuong": payload.get("TenDuong"),
                "Phuong": payload.get("Phuong"),
                "QuanHuyen": payload.get("QuanHuyen"),
                "TinhThanh": payload.get("TinhThanh"),
            }
        }

        if matched:
            response["matched_info"] = {
                "TenDuong": source_row.get(
                    "TenDuong_",
                    source_row.get("TenDuong")
                ),
                "QuanHuyen": source_row.get(
                    "QuanHuyen_",
                    source_row.get("QuanHuyen")
                ),
                "Phuong": source_row.get(
                    "Phuong_",
                    source_row.get("Phuong")
                ),
                "TuDiem": source_row.get("TuDiem"),
                "DenDiem": source_row.get("DenDiem"),
                "X": source_row.get("X"),
                "Y": source_row.get("Y"),
            }

        return response

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc(),
            },
        )
    
load_source_data()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000
    )