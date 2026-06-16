# -*- coding: utf-8 -*-
"""
train_land_price_two_targets_v2.py

Mục tiêu:
- Giữ nguyên 2 target: GiaDat2019, GiaDat2025
- Giữ spatial holdout + GroupKFold để tránh leakage không gian
- Tối ưu để KHỚP với API predict hiện tại:
  * API gửi: Latitude, Longitude, TenDuong, Phuong, QuanHuyen, nearbyRoads, nearbyPlaces
  * Script này chuẩn hoá dữ liệu để train và predict dùng cùng schema
- Ưu tiên tăng trọng số cho TenDuong / QuanHuyen / Phuong bằng feature engineering,
  nhưng không thêm feature “lạ” khiến API không match được.

Chạy:
    python train_land_price_two_targets_v2.py

Có thể ghi đè đường dẫn bằng biến môi trường:
    LAND_PRICE_EXCEL=./model/HCM.xlsx
    LAND_PRICE_SHEET=road_giadat_tphcm
    LAND_PRICE_MODEL_DIR=./model/artifacts

Cài đặt:
    pip install pandas numpy scikit-learn scipy catboost joblib openpyxl
"""

from __future__ import annotations

import json
import math
import os
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from scipy.stats import pearsonr
from sklearn.cluster import KMeans
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold

warnings.filterwarnings("ignore")

# =========================
# Config
# =========================
EXCEL_PATH = Path(os.environ.get("LAND_PRICE_EXCEL", "./model/HCM.xlsx"))
SHEET_NAME = os.environ.get("LAND_PRICE_SHEET", "road_giadat_tphcm")
MODEL_DIR = Path(os.environ.get("LAND_PRICE_MODEL_DIR", "./model/artifacts"))

TARGET_COLS = ["GiaDat2019", "GiaDat2025"]

RANDOM_STATE = 42
N_CLUSTERS = 60
TEST_CLUSTER_FRACTION = 0.20
N_SPLITS_CV = 5

USE_LOG_TARGET = True
MIN_TARGET_VALUE = 1e-9

MODEL_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# Helpers
# =========================
def normalize_text(value) -> str:
    """Chuẩn hoá giống API: không đổi case, chỉ dọn khoảng trắng."""
    if pd.isna(value):
        return ""
    s = str(value)
    s = s.replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())
    return s.strip()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def safe_mean(values: List[Optional[float]]) -> Optional[float]:
    arr = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(arr)) if arr else None


def compute_metrics(y_true, y_pred) -> Dict[str, Optional[float]]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    mse = mean_squared_error(y_true, y_pred)
    rmse = math.sqrt(mse)
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)

    try:
        r, p = pearsonr(y_true, y_pred)
        if not np.isfinite(r):
            r = None
        if not np.isfinite(p):
            p = None
    except Exception:
        r, p = None, None

    denom = np.where(np.abs(y_true) < 1e-12, np.nan, y_true)
    mape = np.nanmean(np.abs((y_true - y_pred) / denom)) * 100.0

    return {
        "R2": float(r2),
        "Pearson_r": None if r is None else float(r),
        "Pearson_p_value": None if p is None else float(p),
        "MSE": float(mse),
        "RMSE": float(rmse),
        "MAE": float(mae),
        "MAPE_percent": float(mape),
    }


def split_words(value: str) -> int:
    value = normalize_text(value)
    return 0 if not value else len(value.split())


# =========================
# Canonical schema
# =========================
# Ưu tiên các feature mà API có thể cung cấp trực tiếp hoặc suy ra an toàn.
# Những cột trong Excel không có ở API (fclass, name, ref, bridge, tunnel, ...)
# sẽ không đưa vào để tránh train/predict mismatch.
CANONICAL_TEXT_COLS = [
    "TenDuong",
    "QuanHuyen",
    "Phuong",
    "ThanhPho",
    "TinhThanh",
    "QuocGia",
]

CANONICAL_NUMERIC_COLS = [
    "X",
    "Y",
    "road_count",
    "place_count",
    "nearest_road_distance",
    "nearest_place_distance",
]

DERIVED_TEXT_COLS = [
    "road_key",
    "road_district_key",
    "road_ward_key",
    "district_ward_key",
    "road_full_key",
    "TenDuong_prefix",
    "TenDuong_suffix",
]

DERIVED_NUMERIC_COLS = [
    "road_name_len",
    "road_name_word_count",
    "district_len",
    "district_word_count",
    "ward_len",
    "ward_word_count",
    "road_key_len",
    "road_key_word_count",
    "x_round_3",
    "y_round_3",
]

ALL_FEATURE_COLS = CANONICAL_TEXT_COLS + CANONICAL_NUMERIC_COLS + DERIVED_TEXT_COLS + DERIVED_NUMERIC_COLS

# cột không được dùng làm feature
DEFAULT_DROP_COLS = {
    "fid",
    "osm_id",
    "code",
    "STT",
    "STT_2",
    "target_log",
    "GiaDat2019",
    "GiaDat2025",
}

def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Chuẩn hoá tên cột từ Excel / API về schema chung.
    Không đổi tên feature đã chuẩn nếu cột đó đã tồn tại.
    """
    df = df.copy()

    # alias cho ward
    if "Phuong" not in df.columns and "Phường" in df.columns:
        df["Phuong"] = df["Phường"]
    if "Phường" not in df.columns and "Phuong" in df.columns:
        df["Phường"] = df["Phuong"]

    # alias cho tọa độ nếu có
    if "X" not in df.columns and "Longitude" in df.columns:
        df["X"] = df["Longitude"]
    if "Y" not in df.columns and "Latitude" in df.columns:
        df["Y"] = df["Latitude"]

    # runtime optional fields
    if "road_count" not in df.columns:
        df["road_count"] = np.nan
    if "place_count" not in df.columns:
        df["place_count"] = np.nan
    if "nearest_road_distance" not in df.columns:
        df["nearest_road_distance"] = np.nan
    if "nearest_place_distance" not in df.columns:
        df["nearest_place_distance"] = np.nan

    # optional text fields
    for col in ["ThanhPho", "TinhThanh", "QuocGia"]:
        if col not in df.columns:
            df[col] = ""

    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tạo feature thống nhất cho train và predict.
    Feature chỉ dựa trên những gì API có thể gửi + vài đặc trưng suy ra an toàn.
    """
    df = canonicalize_columns(df).copy()

    # text normalization
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

    # numeric normalization
    for col in ["X", "Y", "road_count", "place_count", "nearest_road_distance", "nearest_place_distance"]:
        df[col] = safe_numeric(df[col])

    # ---- derived text keys ----
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

    # ---- derived numeric features ----
    df["road_name_len"] = df["TenDuong"].fillna("").astype(str).str.len()
    df["road_name_word_count"] = df["TenDuong"].fillna("").astype(str).str.split().str.len()

    df["district_len"] = df["QuanHuyen"].fillna("").astype(str).str.len()
    df["district_word_count"] = df["QuanHuyen"].fillna("").astype(str).str.split().str.len()

    df["ward_len"] = df["Phuong"].fillna("").astype(str).str.len()
    df["ward_word_count"] = df["Phuong"].fillna("").astype(str).str.split().str.len()

    df["road_key_len"] = df["road_key"].fillna("").astype(str).str.len()
    df["road_key_word_count"] = df["road_key"].fillna("").astype(str).str.split().str.len()

    # round coord as categorical-like location bucket
    df["x_round_3"] = df["X"].round(3)
    df["y_round_3"] = df["Y"].round(3)

    # fill all object dtypes with string
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].fillna("").astype(str)

    return df


def prepare_target_df(df: pd.DataFrame, target_col: str) -> pd.DataFrame:
    if target_col not in df.columns:
        raise ValueError(f"Không tìm thấy cột mục tiêu: {target_col}")

    work_df = df[df[target_col].notna()].copy()
    work_df = work_df[work_df[target_col] > MIN_TARGET_VALUE].copy()

    if work_df.empty:
        raise ValueError(f"Dữ liệu sau khi lọc target {target_col} bị rỗng.")

    if USE_LOG_TARGET:
        work_df["target_log"] = np.log1p(work_df[target_col].astype(float))
    else:
        work_df["target_log"] = work_df[target_col].astype(float)

    return work_df


def add_spatial_clusters(df: pd.DataFrame, n_clusters: int = N_CLUSTERS) -> Tuple[pd.DataFrame, KMeans]:
    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("Cần có cả cột X và Y để tạo spatial clusters.")

    coords = df[["X", "Y"]].copy()
    coords["X"] = coords["X"].fillna(coords["X"].median())
    coords["Y"] = coords["Y"].fillna(coords["Y"].median())

    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=RANDOM_STATE,
        n_init="auto",
    )

    out = df.copy()
    out["cluster"] = kmeans.fit_predict(coords)
    return out, kmeans


def spatial_holdout_split(df: pd.DataFrame, test_cluster_fraction: float = TEST_CLUSTER_FRACTION) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chọn cụm test theo cách ổn định:
    - shuffle cụm
    - cộng dồn theo kích thước cụm đến khi đạt gần tỉ lệ mong muốn
    """
    if "cluster" not in df.columns:
        raise ValueError("Thiếu cột cluster.")

    cluster_sizes = df["cluster"].value_counts().sort_values(ascending=False)
    clusters = cluster_sizes.index.to_list()

    rng = np.random.RandomState(RANDOM_STATE)
    rng.shuffle(clusters)

    target_test_rows = max(1, int(round(len(df) * test_cluster_fraction)))
    chosen = []
    running = 0

    for c in clusters:
        chosen.append(c)
        running += int(cluster_sizes.loc[c])
        if running >= target_test_rows:
            break

    test_clusters = set(chosen)
    train_df = df[~df["cluster"].isin(test_clusters)].copy()
    test_df = df[df["cluster"].isin(test_clusters)].copy()

    return train_df, test_df


def select_feature_cols(df: pd.DataFrame, target_col: str) -> Tuple[List[str], List[str]]:
    drop_cols = set(DEFAULT_DROP_COLS)
    drop_cols.discard(target_col)

    # Không dùng target còn lại làm feature
    for t in TARGET_COLS:
        if t != target_col:
            drop_cols.add(t)

    # Chỉ giữ feature trong schema chuẩn
    feature_cols = [c for c in ALL_FEATURE_COLS if c in df.columns and c not in drop_cols]
    cat_cols = [c for c in feature_cols if df[c].dtype == "object"]
    return feature_cols, cat_cols


def make_catboost_pool(X: pd.DataFrame, y: Optional[pd.Series], cat_cols: List[str]) -> Pool:
    return Pool(X, y, cat_features=cat_cols)


def train_one_target(df: pd.DataFrame, target_col: str) -> Dict:
    print(f"\n{'=' * 100}")
    print(f"TRAIN TARGET: {target_col}")
    print(f"{'=' * 100}")

    work_df = prepare_target_df(df, target_col)

    # Chuẩn hoá feature trước
    work_df = build_features(work_df)

    # tạo cụm không gian từ tọa độ thật
    work_df, kmeans = add_spatial_clusters(work_df, n_clusters=N_CLUSTERS)

    # chọn feature
    feature_cols, cat_cols = select_feature_cols(work_df, target_col)
    if not feature_cols:
        raise ValueError("Không còn feature nào sau khi loại cột.")

    # debug output
    print("Feature cols:", feature_cols)
    print("Cat cols:", cat_cols)

    # holdout split theo cluster
    train_df, test_df = spatial_holdout_split(work_df, test_cluster_fraction=TEST_CLUSTER_FRACTION)

    if train_df.empty or test_df.empty:
        raise ValueError("Train/test sau spatial split bị rỗng. Hãy giảm N_CLUSTERS hoặc TEST_CLUSTER_FRACTION.")

    X_train = train_df[feature_cols].copy()
    y_train = train_df["target_log"].copy()

    X_test = test_df[feature_cols].copy()
    y_test = test_df["target_log"].copy()

    train_pool = make_catboost_pool(X_train, y_train, cat_cols)
    test_pool = make_catboost_pool(X_test, y_test, cat_cols)

    model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=5000,
        learning_rate=0.025,
        depth=10,                 # sâu hơn để học tổ hợp TenDuong + QuanHuyen + Phuong tốt hơn
        l2_leaf_reg=6,
        random_seed=RANDOM_STATE,
        subsample=0.88,
        rsm=0.88,
        bagging_temperature=0.4,
        od_type="Iter",
        od_wait=250,
        min_data_in_leaf=15,
        verbose=200,
    )

    model.fit(train_pool, eval_set=test_pool, use_best_model=True)

    # ---- Holdout metrics ----
    pred_test_log = model.predict(X_test)
    if USE_LOG_TARGET:
        y_true_test = np.expm1(np.asarray(y_test, dtype=float))
        y_pred_test = np.expm1(np.asarray(pred_test_log, dtype=float))
    else:
        y_true_test = np.asarray(y_test, dtype=float)
        y_pred_test = np.asarray(pred_test_log, dtype=float)

    holdout_metrics = compute_metrics(y_true_test, y_pred_test)

    print("\n===== SPATIAL HOLD-OUT TEST METRICS =====")
    print(json.dumps(holdout_metrics, ensure_ascii=False, indent=2))

    # ---- GroupKFold CV ----
    print("\n===== 5-FOLD GROUP CV =====")
    gkf = GroupKFold(n_splits=N_SPLITS_CV)
    fold_metrics = []

    X_all = work_df[feature_cols].reset_index(drop=True)
    y_all = work_df["target_log"].reset_index(drop=True)
    groups = work_df["cluster"].reset_index(drop=True)

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_all, y_all, groups), start=1):
        X_tr, X_va = X_all.iloc[tr_idx], X_all.iloc[va_idx]
        y_tr, y_va = y_all.iloc[tr_idx], y_all.iloc[va_idx]

        tr_pool = make_catboost_pool(X_tr, y_tr, cat_cols)
        va_pool = make_catboost_pool(X_va, y_va, cat_cols)

        fold_model = CatBoostRegressor(
            loss_function="RMSE",
            eval_metric="RMSE",
            iterations=3000,
            learning_rate=0.025,
            depth=10,
            l2_leaf_reg=6,
            random_seed=RANDOM_STATE,
            subsample=0.88,
            rsm=0.88,
            bagging_temperature=0.4,
            od_type="Iter",
            od_wait=180,
            min_data_in_leaf=15,
            verbose=False,
        )

        fold_model.fit(tr_pool, eval_set=va_pool, use_best_model=True)
        pred_va_log = fold_model.predict(X_va)

        if USE_LOG_TARGET:
            true_va = np.expm1(np.asarray(y_va, dtype=float))
            pred_va = np.expm1(np.asarray(pred_va_log, dtype=float))
        else:
            true_va = np.asarray(y_va, dtype=float)
            pred_va = np.asarray(pred_va_log, dtype=float)

        m = compute_metrics(true_va, pred_va)
        fold_metrics.append(m)
        print(f"Fold {fold}: {json.dumps(m, ensure_ascii=False)}")

    cv_summary = {k: safe_mean([fm.get(k) for fm in fold_metrics]) for k in fold_metrics[0].keys()}

    print("\n===== 5-FOLD GROUP CV AVERAGE =====")
    print(json.dumps(cv_summary, ensure_ascii=False, indent=2))

    # ---- final model on full data ----
    final_pool = make_catboost_pool(work_df[feature_cols], work_df["target_log"], cat_cols)
    final_model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=int(model.get_params().get("iterations", 5000)),
        learning_rate=0.025,
        depth=10,
        l2_leaf_reg=6,
        random_seed=RANDOM_STATE,
        subsample=0.88,
        rsm=0.88,
        bagging_temperature=0.4,
        od_type="Iter",
        od_wait=250,
        min_data_in_leaf=15,
        verbose=200,
    )
    final_model.fit(final_pool)

    importance = final_model.get_feature_importance(final_pool)
    imp_df = (
        pd.DataFrame({"feature": feature_cols, "importance": importance})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )

    print("\n===== TOP 20 FEATURES =====")
    print(imp_df.head(20).to_string(index=False))

    # ---- save artifacts ----
    model_path = MODEL_DIR / f"catboost_spatial_{target_col}.cbm"
    meta_path = MODEL_DIR / f"catboost_spatial_{target_col}_meta.json"
    features_path = MODEL_DIR / f"catboost_spatial_{target_col}_features.json"
    clusterer_path = MODEL_DIR / f"catboost_spatial_{target_col}_kmeans.joblib"
    importance_path = MODEL_DIR / f"catboost_spatial_{target_col}_feature_importance.csv"

    final_model.save_model(str(model_path))
    joblib.dump(kmeans, clusterer_path)
    imp_df.to_csv(importance_path, index=False, encoding="utf-8-sig")

    meta = {
        "excel_path": str(EXCEL_PATH),
        "sheet_name": SHEET_NAME,
        "target_col": target_col,
        "random_state": RANDOM_STATE,
        "use_log_target": USE_LOG_TARGET,
        "n_clusters": N_CLUSTERS,
        "test_cluster_fraction": TEST_CLUSTER_FRACTION,
        "n_splits_cv": N_SPLITS_CV,
        "feature_cols": feature_cols,
        "cat_cols": cat_cols,
        "holdout_metrics": holdout_metrics,
        "cv_average_metrics": cv_summary,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_clusters": sorted(train_df["cluster"].unique().tolist()),
        "test_clusters": sorted(test_df["cluster"].unique().tolist()),
        "schema_note": (
            "Feature schema được chuẩn hoá để API predict có thể reindex theo feature_cols "
            "mà không bị lỗi thiếu cột. Các cột không có ở runtime sẽ tự fill rỗng/0."
        ),
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    features_path.write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nĐã lưu model:      {model_path}")
    print(f"Đã lưu meta:       {meta_path}")
    print(f"Đã lưu features:   {features_path}")
    print(f"Đã lưu KMeans:     {clusterer_path}")
    print(f"Đã lưu importance: {importance_path}")

    return {
        "target_col": target_col,
        "model_path": str(model_path),
        "meta_path": str(meta_path),
        "features_path": str(features_path),
        "clusterer_path": str(clusterer_path),
        "importance_path": str(importance_path),
        "holdout_metrics": holdout_metrics,
        "cv_average_metrics": cv_summary,
        "top_features": imp_df.head(20).to_dict(orient="records"),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
    }


def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Không tìm thấy file Excel: {EXCEL_PATH}")

    df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
    df = canonicalize_columns(df)

    # kiểm tra tối thiểu
    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("Cần tối thiểu 2 cột X, Y (hoặc Longitude, Latitude) để huấn luyện spatial model.")
    if "TenDuong" not in df.columns or "QuanHuyen" not in df.columns:
        raise ValueError("Cần có TenDuong và QuanHuyen để train model ổn định.")

    results = {}
    for target_col in TARGET_COLS:
        results[target_col] = train_one_target(df, target_col)

    summary_path = MODEL_DIR / "training_summary_spatial_v2.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã lưu tổng hợp kết quả tại: {summary_path}")


if __name__ == "__main__":
    main()
