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


def load_json_file(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


for target in TARGETS:
    model = CatBoostRegressor()
    model.load_model(str(MODEL_DIR/f"catboost_spatial_{target}.cbm"))
    models[target] = model
    feature_lists[target] = load_json_file(MODEL_DIR/f"catboost_spatial_{target}_features.json")
    metas[target] = load_json_file(MODEL_DIR/f"catboost_spatial_{target}_meta.json")


def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())
    return s.strip()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "Phuong" not in df.columns and "Phường" in df.columns:
        df["Phuong"] = df["Phường"]

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

    for col in ["ThanhPho", "TinhThanh", "QuocGia"]:
        if col not in df.columns:
            df[col] = ""

    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = canonicalize_columns(df).copy()

    text_like_cols = [
        "TenDuong",
        "QuanHuyen",
        "Phuong",
        "ThanhPho",
        "TinhThanh",
        "QuocGia",
    ]

    for col in text_like_cols:
        df[col] = df[col].map(normalize_text)

    for col in ["X", "Y", "road_count", "place_count", "nearest_road_distance", "nearest_place_distance"]:
        df[col] = safe_numeric(df[col])

    df["road_key"] = (
        df["TenDuong"].fillna("") + "|" +
        df["QuanHuyen"].fillna("") + "|" +
        df["Phuong"].fillna("")
    ).map(normalize_text)

    df["road_district_key"] = (
        df["TenDuong"].fillna("") + "|" +
        df["QuanHuyen"].fillna("")
    ).map(normalize_text)

    df["road_ward_key"] = (
        df["TenDuong"].fillna("") + "|" +
        df["Phuong"].fillna("")
    ).map(normalize_text)

    df["district_ward_key"] = (
        df["QuanHuyen"].fillna("") + "|" +
        df["Phuong"].fillna("")
    ).map(normalize_text)

    df["road_full_key"] = (
        df["TenDuong"].fillna("") + "|" +
        df["QuanHuyen"].fillna("") + "|" +
        df["Phuong"].fillna("") + "|" +
        df["ThanhPho"].fillna("") + "|" +
        df["TinhThanh"].fillna("")
    ).map(normalize_text)

    def first_token(s: str) -> str:
        s = normalize_text(s)
        return s.split()[0] if s else ""

    def last_token(s: str) -> str:
        s = normalize_text(s)
        return s.split()[-1] if s else ""

    df["TenDuong_prefix"] = df["TenDuong"].map(first_token)
    df["TenDuong_suffix"] = df["TenDuong"].map(last_token)

    df["road_name_len"] = df["TenDuong"].fillna("").astype(str).str.len()
    df["road_name_word_count"] = df["TenDuong"].fillna("").astype(str).str.split().str.len()

    df["district_len"] = df["QuanHuyen"].fillna("").astype(str).str.len()
    df["district_word_count"] = df["QuanHuyen"].fillna("").astype(str).str.split().str.len()

    df["ward_len"] = df["Phuong"].fillna("").astype(str).str.len()
    df["ward_word_count"] = df["Phuong"].fillna("").astype(str).str.split().str.len()

    df["road_key_len"] = df["road_key"].fillna("").astype(str).str.len()
    df["road_key_word_count"] = df["road_key"].fillna("").astype(str).str.split().str.len()

    df["x_round_3"] = df["X"].round(3)
    df["y_round_3"] = df["Y"].round(3)

    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) == "category":
            df[col] = df[col].fillna("").astype(str)

    return df

def create_runtime_features(payload: dict) -> dict:
    roads = payload.get("nearbyRoads") or []
    places = payload.get("nearbyPlaces") or []

    row = {
        "TenDuong": payload.get("TenDuong") or "",
        "QuanHuyen": payload.get("QuanHuyen") or "",
        "Phuong": payload.get("Phuong") or "",
        "ThanhPho": payload.get("ThanhPho") or "",
        "TinhThanh": payload.get("TinhThanh") or "",
        "QuocGia": payload.get("QuocGia") or "VN",
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
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0)

    return X


@app.get("/health")
def health():
    return {"ok": True, "message": "ML service is running"}


@app.post("/predict")
def predict(req: PredictRequest):
    try:
        payload = req.model_dump()
        row = create_runtime_features(payload)
        df = pd.DataFrame([row])
        df = build_features(df)

        result = {}

        for target in TARGETS:
            feature_cols = feature_lists[target]
            cat_cols = metas[target].get("cat_cols", [])

            X = prepare_model_input(df, feature_cols, cat_cols)

            print("\n=== TARGET:", target, "===")
            print("First 20 features:", feature_cols[:20])
            print("cat_cols:", cat_cols[:20] if isinstance(cat_cols, list) else cat_cols)
            print(X.dtypes.head(20))
            print(X.iloc[0].head(20))

            pred = models[target].predict(X)[0]

            if metas[target].get("use_log_target", False):
                pred = np.expm1(pred)

            result[target] = round(float(pred), 0)

        return {
            "ok": True,
            "prediction": result,
            "GiaDat2019": result.get("GiaDat2019"),
            "GiaDat2025": result.get("GiaDat2025"),
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc(),
            },
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000
    )