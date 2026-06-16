# -*- coding: utf-8 -*-
"""
train_land_price_spatial_model.py

Huấn luyện mô hình giá đất theo hướng hợp lý hơn cho dữ liệu GIS:
- 2 target: GiaDat2019, GiaDat2025
- split theo không gian (spatial holdout) để giảm leakage
- 5-fold GroupKFold theo cụm không gian
- CatBoostRegressor cho dữ liệu tabular + categorical
- đánh giá: R2, Pearson r, MSE, RMSE, MAE, MAPE
- lưu model, meta, feature list, clusterer

Điểm khác so với bản cũ:
- không dùng random train_test_split mặc định
- tạo cụm không gian từ X/Y rồi tách train/test theo cluster
- CV cũng dùng GroupKFold
- loại bỏ cột có nguy cơ leak rõ rệt
- có thể tùy chỉnh số cluster, tỷ lệ test theo cụm, và các cột cần loại bỏ

Cách chạy:
    python train_land_price_spatial_model.py

Yêu cầu:
    pip install pandas numpy scikit-learn scipy catboost joblib openpyxl
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, asdict
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


# Cấu hình
EXCEL_PATH = Path("./model/HCM.xlsx")
SHEET_NAME = "road_giadat_tphcm"
TARGET_COLS = ["GiaDat2019", "GiaDat2025"]

RANDOM_STATE = 42
N_CLUSTERS = 60               # tăng/giảm tùy độ lớn dữ liệu
TEST_CLUSTER_FRACTION = 0.20  # lấy khoảng 20% cụm làm holdout
N_SPLITS_CV = 5

USE_LOG_TARGET = True
MIN_TARGET_VALUE = 1e-9

MODEL_DIR = Path("./model/artifacts")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


TEXT_COLS = [
    "fclass", "name", "ref", "oneway", "bridge", "tunnel", "TenJoin",
    "NamApDung", "QuanHuyen", "TenDuong", "Phường", "TuDiem", "DenDiem",
    "TenDuong_2", "TuDiem_2", "DenDiem_2", "QuanHuyen_", "DiaChi",
]

NUMERIC_COLS = ["X", "Y", "maxspeed", "layer", "STT", "STT_2"]

# Loại bỏ các cột train mô hình ko hiệu quả
DEFAULT_DROP_COLS = {
    "target_log",
    "fid", "osm_id", "code",
    "STT", "STT_2",  
    "GiaDat2019", "GiaDat2025",
}

# Các đặc trưng khác có thể dùng nếu bạn phát triển thêm PostGIS/nearby features
OPTIONAL_NUMERIC_PREFIXES = (
    "dist_", "distance_", "near_", "nearest_", "road_cnt_", "place_cnt_",
    "road_count_", "place_count_", "avg_", "mean_", "median_", "min_", "max_",
    "std_", "weighted_", "knearest_",
)

def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())
    return s.strip()


def safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


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


def safe_mean(values: List[Optional[float]]) -> Optional[float]:
    arr = [v for v in values if v is not None and np.isfinite(v)]
    if not arr:
        return None
    return float(np.mean(arr))

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # chuẩn hóa text
    for col in TEXT_COLS:
        if col in df.columns:
            df[col] = df[col].map(normalize_text)

    # ép số
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = safe_numeric(df[col])

    for col in TARGET_COLS:
        if col in df.columns:
            df[col] = safe_numeric(df[col])

    # đặc trưng đơn giản, ổn định
    if "TenDuong" in df.columns:
        df["road_name_len"] = df["TenDuong"].fillna("").astype(str).str.len()
        df["road_name_word_count"] = df["TenDuong"].fillna("").astype(str).str.split().str.len()
    else:
        df["road_name_len"] = 0
        df["road_name_word_count"] = 0

    if "QuanHuyen" in df.columns:
        df["district_len"] = df["QuanHuyen"].fillna("").astype(str).str.len()
        df["district_word_count"] = df["QuanHuyen"].fillna("").astype(str).str.split().str.len()
    else:
        df["district_len"] = 0
        df["district_word_count"] = 0

    # tọa độ
    if "X" in df.columns and "Y" in df.columns:
        df["xy_sum"] = df["X"].fillna(0) + df["Y"].fillna(0)
        df["xy_diff"] = df["X"].fillna(0) - df["Y"].fillna(0)
        df["lat_abs"] = df["Y"].abs()
        df["lon_abs"] = df["X"].abs()
        df["xy_radius"] = np.sqrt(df["X"].fillna(0) ** 2 + df["Y"].fillna(0) ** 2)
    else:
        df["xy_sum"] = 0
        df["xy_diff"] = 0
        df["lat_abs"] = 0
        df["lon_abs"] = 0
        df["xy_radius"] = 0

    # tự động ép numeric cho các cột hữu ích
    for col in df.columns:
        if col.startswith(OPTIONAL_NUMERIC_PREFIXES):
            df[col] = safe_numeric(df[col])

    # làm sạch text NaN
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].fillna("")

    return df


# Chọn các đặc trưng cho huấn luyện
def select_feature_cols(df: pd.DataFrame, target_col: str) -> Tuple[List[str], List[str]]:
    drop_cols = set(DEFAULT_DROP_COLS)
    drop_cols.discard(target_col)

    for t in TARGET_COLS:
        if t != target_col:
            drop_cols.add(t)

    feature_cols = [c for c in df.columns if c not in drop_cols]
    cat_cols = [c for c in feature_cols if df[c].dtype == "object"]
    return feature_cols, cat_cols


def add_spatial_clusters(df: pd.DataFrame, n_clusters: int = N_CLUSTERS) -> Tuple[pd.DataFrame, KMeans]:
    work_df = df.copy()

    if "X" not in work_df.columns or "Y" not in work_df.columns:
        raise ValueError("Cần có cả cột X và Y để tạo spatial clusters.")

    coords = work_df[["X", "Y"]].copy()
    coords["X"] = coords["X"].fillna(coords["X"].median())
    coords["Y"] = coords["Y"].fillna(coords["Y"].median())

    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=RANDOM_STATE,
        n_init="auto",
    )
    work_df["cluster"] = kmeans.fit_predict(coords)
    return work_df, kmeans


def spatial_holdout_split(df: pd.DataFrame, test_cluster_fraction: float = TEST_CLUSTER_FRACTION) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tách train/test theo cụm không gian.
    Mục tiêu: test nằm ở các cluster khác train để giảm lộ thông tin không gian.
    """
    if "cluster" not in df.columns:
        raise ValueError("Thiếu cột cluster.")

    cluster_counts = df["cluster"].value_counts().sort_index()
    clusters = cluster_counts.index.to_list()

    # chọn cụm test sao cho số lượng tương đối ổn định
    total = len(clusters)
    n_test_clusters = max(1, int(round(total * test_cluster_fraction)))

    # sắp clusters theo thứ tự ngẫu nhiên nhưng ổn định
    rng = np.random.RandomState(RANDOM_STATE)
    shuffled = np.array(clusters)
    rng.shuffle(shuffled)
    test_clusters = set(shuffled[:n_test_clusters].tolist())

    train_df = df[~df["cluster"].isin(test_clusters)].copy()
    test_df = df[df["cluster"].isin(test_clusters)].copy()

    return train_df, test_df


#Chọn cột mục tiêu đầu ra mô hình
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

#Huấn luyện mô hình
def train_one_target(df: pd.DataFrame, target_col: str) -> Dict:
    print(f"\n{'=' * 90}")
    print(f"TRAIN TARGET: {target_col}")
    print(f"{'=' * 90}")

    work_df = prepare_target_df(df, target_col)

    feature_cols, cat_cols = select_feature_cols(work_df, target_col)
    if not feature_cols:
        raise ValueError("Không còn feature nào sau khi loại cột.")

    # tạo cluster không gian trên toàn bộ tập có target
    work_df, kmeans = add_spatial_clusters(work_df, n_clusters=N_CLUSTERS)

    # split holdout theo cluster
    train_df, test_df = spatial_holdout_split(work_df, test_cluster_fraction=TEST_CLUSTER_FRACTION)

    if train_df.empty or test_df.empty:
        raise ValueError("Train/test sau spatial split bị rỗng. Hãy giảm N_CLUSTERS hoặc TEST_CLUSTER_FRACTION.")

    X_train = train_df[feature_cols].copy()
    y_train = train_df["target_log"].copy()
    g_train = train_df["cluster"].copy()

    X_test = test_df[feature_cols].copy()
    y_test = test_df["target_log"].copy()
    g_test = test_df["cluster"].copy()

    train_pool = Pool(X_train, y_train, cat_features=cat_cols)
    test_pool = Pool(X_test, y_test, cat_features=cat_cols)

    model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=4000,
        learning_rate=0.03,
        depth=8,
        l2_leaf_reg=6,
        random_seed=RANDOM_STATE,
        subsample=0.85,
        rsm=0.85,
        bagging_temperature=0.5,
        od_type="Iter",
        od_wait=200,
        min_data_in_leaf=20,
        verbose=200,
    )

    model.fit(train_pool, eval_set=test_pool, use_best_model=True)

    # holdout metrics
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

    # 5-fold spatial CV bằng GroupKFold
    print("\n===== 5-FOLD GROUP CV =====")
    gkf = GroupKFold(n_splits=N_SPLITS_CV)
    fold_metrics = []

    X_all = work_df[feature_cols].reset_index(drop=True)
    y_all = work_df["target_log"].reset_index(drop=True)
    groups = work_df["cluster"].reset_index(drop=True)

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_all, y_all, groups), start=1):
        X_tr, X_va = X_all.iloc[tr_idx], X_all.iloc[va_idx]
        y_tr, y_va = y_all.iloc[tr_idx], y_all.iloc[va_idx]

        tr_pool = Pool(X_tr, y_tr, cat_features=cat_cols)
        va_pool = Pool(X_va, y_va, cat_features=cat_cols)

        fold_model = CatBoostRegressor(
            loss_function="RMSE",
            eval_metric="RMSE",
            iterations=2500,
            learning_rate=0.03,
            depth=8,
            l2_leaf_reg=6,
            random_seed=RANDOM_STATE,
            subsample=0.85,
            rsm=0.85,
            bagging_temperature=0.5,
            od_type="Iter",
            od_wait=150,
            min_data_in_leaf=20,
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

    # retrain final model trên toàn bộ work_df để lưu model cuối cùng
    final_pool = Pool(work_df[feature_cols], work_df["target_log"], cat_features=cat_cols)
    final_model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=int(model.get_params().get("iterations", 4000)),
        learning_rate=0.03,
        depth=8,
        l2_leaf_reg=6,
        random_seed=RANDOM_STATE,
        subsample=0.85,
        rsm=0.85,
        bagging_temperature=0.5,
        od_type="Iter",
        od_wait=200,
        min_data_in_leaf=20,
        verbose=200,
    )
    final_model.fit(final_pool)

    # feature importance
    importance = final_model.get_feature_importance(final_pool)
    imp_df = pd.DataFrame({"feature": feature_cols, "importance": importance}).sort_values(
        "importance", ascending=False
    )

    print("\n===== TOP 20 FEATURES =====")
    print(imp_df.head(20).to_string(index=False))

    # lưu artifacts
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
        "note": (
            "Model train theo spatial split + GroupKFold. "
            "Nếu USE_LOG_TARGET=True thì model học log1p(target), dự đoán sẽ expm1 về thang gốc."
        ),
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    features_path.write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nĐã lưu model:     {model_path}")
    print(f"Đã lưu meta:      {meta_path}")
    print(f"Đã lưu features:  {features_path}")
    print(f"Đã lưu KMeans:    {clusterer_path}")
    print(f"Đã lưu importance:{importance_path}")

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
    df = build_features(df)

    # kiểm tra cột tọa độ
    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("Cần tối thiểu 2 cột X, Y để huấn luyện spatial model.")

    results = {}
    for target_col in TARGET_COLS:
        results[target_col] = train_one_target(df, target_col)

    summary_path = MODEL_DIR / "training_summary_spatial.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã lưu tổng hợp kết quả tại: {summary_path}")


if __name__ == "__main__":
    main()
