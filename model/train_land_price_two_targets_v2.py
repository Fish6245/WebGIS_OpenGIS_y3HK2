from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.cluster import KMeans
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_squared_error, mean_absolute_error

warnings.filterwarnings("ignore")

# =========================
# CONFIG
# =========================
MODEL_DIR = Path("./model/artifacts")
EXCEL_FILE = Path("./model/HCM.xlsx")
SHEET_NAME = "road_giadat_tphcm"

TARGETS = ["GiaDat2019", "GiaDat2025"]

RANDOM_STATE = 42
N_CLUSTERS = 60
TEST_CLUSTER_FRACTION = 0.20
N_SPLITS_CV = 5
USE_LOG_TARGET = True
MIN_TARGET_VALUE = 1e-9

MODEL_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# SERVICE-SYNC HELPERS
# =========================
def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    return " ".join(s.split()).strip()


def safe_numeric(s):
    if isinstance(s, pd.Series):
        s = s.astype(str).str.replace(",", "", regex=False).str.replace(" ", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


def safe_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return df[col].fillna("").astype(str)
    return pd.Series([""] * len(df), index=df.index)


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

    # keep same behavior as service, but make sure both sides exist
    pairs = [
        ("TenDuong", "TenDuong_"),
        ("QuanHuyen", "QuanHuyen_"),
        ("Phuong", "Phuong_"),
        ("ThanhPho", "ThanhPho_"),
        ("TinhThanh", "TinhThanh_"),
        ("QuocGia", "QuocGia_"),
    ]

    for left, right in pairs:
        df = sync_pair(df, left, right)
        if left not in df.columns and right not in df.columns:
            df[left] = ""
            df[right] = ""

    if "X" not in df.columns:
        if "Longitude" in df.columns:
            df["X"] = df["Longitude"]
        else:
            df["X"] = np.nan

    if "Y" not in df.columns:
        if "Latitude" in df.columns:
            df["Y"] = df["Latitude"]
        else:
            df["Y"] = np.nan

    # runtime optional fields must always exist
    for col in [
        "road_count",
        "place_count",
        "nearest_road_distance",
        "nearest_place_distance",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identical spirit to service: same raw text columns, same derived features,
    same runtime placeholders.
    """
    df = canonicalize_columns(df).copy()

    for c in [
        "TenDuong_",
        "QuanHuyen_",
        "Phuong_",
        "ThanhPho_",
        "TinhThanh_",
        "QuocGia_",
    ]:
        df[c] = safe_col(df, c).map(normalize_text)

    for c in [
        "X",
        "Y",
        "road_count",
        "place_count",
        "nearest_road_distance",
        "nearest_place_distance",
    ]:
        df[c] = safe_numeric(df[c])

    road_name = safe_col(df, "TenDuong_")
    district_name = safe_col(df, "QuanHuyen_")
    ward_name = safe_col(df, "Phuong_")
    city_name = safe_col(df, "ThanhPho_")
    province_name = safe_col(df, "TinhThanh_")

    df["road_key"] = (road_name + "|" + district_name + "|" + ward_name).map(normalize_text)
    df["road_district_key"] = (road_name + "|" + district_name).map(normalize_text)
    df["road_ward_key"] = (road_name + "|" + ward_name).map(normalize_text)
    df["district_ward_key"] = (district_name + "|" + ward_name).map(normalize_text)
    df["road_full_key"] = (
        road_name + "|" + district_name + "|" + ward_name + "|" + city_name + "|" + province_name
    ).map(normalize_text)

    def first_token(x: str) -> str:
        x = normalize_text(x)
        return x.split()[0] if x else ""

    def last_token(x: str) -> str:
        x = normalize_text(x)
        return x.split()[-1] if x else ""

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

    df["x_round_3"] = pd.to_numeric(df["X"], errors="coerce").round(3)
    df["y_round_3"] = pd.to_numeric(df["Y"], errors="coerce").round(3)

    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].fillna("").astype(str)

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
        row["nearest_road_distance"] = min(float(r.get("distance_m", 999999)) for r in roads)

    if places:
        row["nearest_place_distance"] = min(float(p.get("distance_m", 999999)) for p in places)

    return row


# =========================
# FEATURE SCHEMA
# =========================
FEATURE_COLS = [
    "TenDuong_",
    "QuanHuyen_",
    "Phuong_",
    "X",
    "Y",
    "road_count",
    "place_count",
    "nearest_road_distance",
    "nearest_place_distance",
    "road_key",
    "road_district_key",
    "road_ward_key",
    "district_ward_key",
    "road_full_key",
    "TenDuong_prefix",
    "TenDuong_suffix",
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

CAT_COLS = [
    "TenDuong_",
    "QuanHuyen_",
    "Phuong_",
    "road_key",
    "road_district_key",
    "road_ward_key",
    "district_ward_key",
    "road_full_key",
    "TenDuong_prefix",
    "TenDuong_suffix",
]


def prepare_model_input(df: pd.DataFrame, feature_cols: list[str], cat_cols: list[str]) -> pd.DataFrame:
    X = df.reindex(columns=feature_cols).copy()
    cat_set = set(cat_cols or [])

    for col in X.columns:
        if col in cat_set:
            X[col] = X[col].apply(lambda v: "" if pd.isna(v) else normalize_text(v)).astype(str)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce")

    return X


# =========================
# SPLIT + METRICS
# =========================
def clean_target_df(df: pd.DataFrame, target_col: str) -> pd.DataFrame:
    if target_col not in df.columns:
        raise ValueError(f"Không tìm thấy cột mục tiêu: {target_col}")

    work = df.copy()
    work[target_col] = safe_numeric(work[target_col])
    work = work[work[target_col].notna()].copy()
    work = work[work[target_col] > MIN_TARGET_VALUE].copy()

    if work.empty:
        raise ValueError(f"Dữ liệu sau khi làm sạch target {target_col} bị rỗng.")

    if USE_LOG_TARGET:
        work["target_log"] = np.log1p(work[target_col].astype(float))
    else:
        work["target_log"] = work[target_col].astype(float)

    return work


def add_spatial_clusters(df: pd.DataFrame, n_clusters: int = N_CLUSTERS) -> Tuple[pd.DataFrame, KMeans]:
    if len(df) < 2:
        raise ValueError("Dữ liệu quá ít để tạo spatial clusters.")

    coords = df[["X", "Y"]].copy()
    coords["X"] = coords["X"].fillna(coords["X"].median() if pd.notna(coords["X"].median()) else 0)
    coords["Y"] = coords["Y"].fillna(coords["Y"].median() if pd.notna(coords["Y"].median()) else 0)

    n_clusters = min(n_clusters, len(df))

    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=RANDOM_STATE,
        n_init="auto",
    )

    out = df.copy()
    out["cluster"] = kmeans.fit_predict(coords)
    return out, kmeans


def spatial_holdout_split(df: pd.DataFrame, test_cluster_fraction: float = TEST_CLUSTER_FRACTION) -> Tuple[pd.DataFrame, pd.DataFrame]:
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

    if train_df.empty or test_df.empty:
        # fallback random split if cluster split becomes degenerate
        shuffled = df.sample(frac=1.0, random_state=RANDOM_STATE)
        split_idx = int(len(shuffled) * (1 - test_cluster_fraction))
        train_df = shuffled.iloc[:split_idx].copy()
        test_df = shuffled.iloc[split_idx:].copy()

    return train_df, test_df


def compute_metrics(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    if len(y_true) == 0:
        return None

    mse = np.mean((y_true - y_pred) ** 2)
    rmse = np.sqrt(mse)
    mae = np.mean(np.abs(y_true - y_pred))

    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0.0

    denom = np.abs(y_true) + 1e-9
    mape = np.mean(np.abs((y_true - y_pred) / denom)) * 100

    acc10 = np.mean(np.abs((y_true - y_pred) / denom) <= 0.1)
    acc20 = np.mean(np.abs((y_true - y_pred) / denom) <= 0.2)

    return {
        "R2": float(r2),
        "RMSE": float(rmse),
        "MAE": float(mae),
        "MAPE_percent": float(mape),
        "ACC10": float(acc10),
        "ACC20": float(acc20),
    }


def safe_mean(values: List[Optional[float]]) -> Optional[float]:
    arr = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(arr)) if arr else None


def average_metric_dicts(metric_dicts: List[Optional[Dict[str, float]]]) -> Optional[Dict[str, float]]:
    clean = [m for m in metric_dicts if isinstance(m, dict)]
    if not clean:
        return None

    keys = clean[0].keys()
    out = {}
    for k in keys:
        vals = [m.get(k) for m in clean]
        out[k] = safe_mean(vals)
    return out


# =========================
# LOOKUP + EVAL
# =========================
def find_best_row_in_df(df: pd.DataFrame, payload: dict) -> Optional[pd.Series]:
    if df is None:
        return None

    ten_duong = normalize_text(payload.get("TenDuong") or "")
    quan_huyen = normalize_text(payload.get("QuanHuyen") or "")

    if not ten_duong or not quan_huyen:
        return None

    work = canonicalize_columns(df.copy())

    road_col = "TenDuong_" if "TenDuong_" in work.columns else "TenDuong"
    district_col = "QuanHuyen_" if "QuanHuyen_" in work.columns else "QuanHuyen"

    work[road_col] = work[road_col].fillna("").astype(str).map(normalize_text)
    work[district_col] = work[district_col].fillna("").astype(str).map(normalize_text)

    mask = (
        work[road_col].str.lower().str.strip() == ten_duong.lower().strip()
    ) & (
        work[district_col].str.lower().str.strip() == quan_huyen.lower().strip()
    )

    matches = work[mask]
    if len(matches) == 0:
        return None

    return matches.iloc[0].copy()


def evaluate_hybrid_on_test(
    model: CatBoostRegressor,
    test_df_feat: pd.DataFrame,
    source_df_raw: pd.DataFrame,
    feature_cols: list[str],
    cat_cols: list[str],
    target_col: str,
) -> Dict:
    lookup_true, lookup_pred = [], []
    model_true, model_pred = [], []
    all_true, all_pred = [], []

    lookup_count = 0
    model_count = 0

    for _, row in test_df_feat.iterrows():
        payload = {
            "TenDuong": row.get("TenDuong_", row.get("TenDuong", "")),
            "QuanHuyen": row.get("QuanHuyen_", row.get("QuanHuyen", "")),
            "Phuong": row.get("Phuong_", row.get("Phuong", "")),
            "Latitude": row.get("Y", None),
            "Longitude": row.get("X", None),
            "ThanhPho": row.get("ThanhPho_", row.get("ThanhPho", "")),
            "TinhThanh": row.get("TinhThanh_", row.get("TinhThanh", "")),
            "QuocGia": row.get("QuocGia_", row.get("QuocGia", "VN")),
            "nearbyRoads": [],
            "nearbyPlaces": [],
        }

        src = find_best_row_in_df(source_df_raw, payload)

        if src is not None:
            lookup_count += 1
            row_df = pd.DataFrame([src])
        else:
            model_count += 1
            row_df = pd.DataFrame([create_runtime_features(payload)])

        row_df = build_features(row_df)
        X_row = prepare_model_input(row_df, feature_cols, cat_cols)

        pred_log = model.predict(X_row)[0]
        pred = np.expm1(pred_log) if USE_LOG_TARGET else float(pred_log)

        true_val = safe_numeric(pd.Series([row[target_col]])).iloc[0]
        if pd.isna(true_val) or true_val <= 0:
            continue

        true_val = float(true_val)

        all_true.append(true_val)
        all_pred.append(float(pred))

        if src is not None:
            lookup_true.append(true_val)
            lookup_pred.append(float(pred))
        else:
            model_true.append(true_val)
            model_pred.append(float(pred))

    total = len(all_true)
    lookup_hit_rate = (lookup_count / total) if total > 0 else 0.0

    lookup_metrics = compute_metrics(lookup_true, lookup_pred)
    model_metrics = compute_metrics(model_true, model_pred)
    hybrid_metrics = compute_metrics(all_true, all_pred)

    return {
        "total_test_rows": int(total),
        "lookup_hit_rate": float(lookup_hit_rate),
        "lookup_branch_count": int(lookup_count),
        "model_branch_count": int(model_count),
        "lookup_branch_metrics": lookup_metrics,
        "model_branch_metrics": model_metrics,
        "hybrid_metrics": hybrid_metrics,
    }


# =========================
# TRAIN ONE TARGET
# =========================
def train_one_target(df: pd.DataFrame, target_col: str) -> Dict:
    print(f"\n{'=' * 100}")
    print(f"TRAIN TARGET: {target_col}")
    print(f"{'=' * 100}")

    # raw cleaned data
    work_raw = clean_target_df(df, target_col)
    work_raw = canonicalize_columns(work_raw)

    # feature data
    work_feat = build_features(work_raw)

    # spatial clusters on feature data
    work_feat, kmeans = add_spatial_clusters(work_feat, n_clusters=N_CLUSTERS)

    # split spatial holdout
    train_feat, test_feat = spatial_holdout_split(work_feat, test_cluster_fraction=TEST_CLUSTER_FRACTION)

    # corresponding raw split for lookup source
    train_raw = work_raw.loc[train_feat.index].copy()
    test_raw = work_raw.loc[test_feat.index].copy()

    if train_feat.empty or test_feat.empty:
        raise ValueError("Train/test sau spatial split bị rỗng.")

    feature_cols = FEATURE_COLS[:]
    cat_cols = CAT_COLS[:]

    print("Feature cols:", feature_cols)
    print("Cat cols:", cat_cols)
    print(f"Train rows: {len(train_feat)} | Test rows: {len(test_feat)}")

    X_train = prepare_model_input(train_feat, feature_cols, cat_cols)
    y_train = train_feat["target_log"].copy()

    X_test = prepare_model_input(test_feat, feature_cols, cat_cols)
    y_test = test_feat["target_log"].copy()

    train_pool = Pool(X_train, y_train, cat_features=cat_cols)
    test_pool = Pool(X_test, y_test, cat_features=cat_cols)

    model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=5000,
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

    model.fit(train_pool, eval_set=test_pool, use_best_model=True)

    # =========================
    # PURE MODEL HOLDOUT METRICS
    # =========================
    pred_test_log = model.predict(X_test)
    if USE_LOG_TARGET:
        y_true_test = np.expm1(np.asarray(y_test, dtype=float))
        y_pred_test = np.expm1(np.asarray(pred_test_log, dtype=float))
    else:
        y_true_test = np.asarray(y_test, dtype=float)
        y_pred_test = np.asarray(pred_test_log, dtype=float)

    model_only_holdout_metrics = compute_metrics(y_true_test, y_pred_test)

    print("\n===== PURE MODEL HOLDOUT METRICS =====")
    print(json.dumps(model_only_holdout_metrics, ensure_ascii=False, indent=2))

    # =========================
    # HYBRID LOOKUP + MODEL EVAL
    # =========================
    print("\n===== LOOKUP / MODEL TEST SUMMARY =====")
    hybrid_summary = evaluate_hybrid_on_test(
        model=model,
        test_df_feat=test_feat,
        source_df_raw=train_raw,
        feature_cols=feature_cols,
        cat_cols=cat_cols,
        target_col=target_col,
    )
    print(json.dumps(hybrid_summary, ensure_ascii=False, indent=2))

    # =========================
    # GROUP K-FOLD CV (MODEL ONLY)
    # =========================
    print("\n===== GROUP K-FOLD CV (MODEL ONLY) =====")
    cv_metrics = []

    n_groups = train_feat["cluster"].nunique()
    n_splits = min(N_SPLITS_CV, n_groups)

    if n_splits >= 2:
        gkf = GroupKFold(n_splits=n_splits)

        X_all = prepare_model_input(train_feat, feature_cols, cat_cols).reset_index(drop=True)
        y_all = train_feat["target_log"].reset_index(drop=True)
        groups = train_feat["cluster"].reset_index(drop=True)

        for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_all, y_all, groups), start=1):
            X_tr, X_va = X_all.iloc[tr_idx], X_all.iloc[va_idx]
            y_tr, y_va = y_all.iloc[tr_idx], y_all.iloc[va_idx]

            tr_pool = Pool(X_tr, y_tr, cat_features=cat_cols)
            va_pool = Pool(X_va, y_va, cat_features=cat_cols)

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
            cv_metrics.append(m)
            print(f"Fold {fold}: {json.dumps(m, ensure_ascii=False)}")
    else:
        print("Không đủ số nhóm để chạy GroupKFold, bỏ qua CV.")

    cv_average_metrics = average_metric_dicts(cv_metrics)

    print("\n===== GROUP K-FOLD CV AVERAGE =====")
    print(json.dumps(cv_average_metrics, ensure_ascii=False, indent=2))

    # =========================
    # FINAL MODEL ON FULL DATA
    # =========================
    final_X = prepare_model_input(work_feat, feature_cols, cat_cols)
    final_y = work_feat["target_log"].copy()

    final_pool = Pool(final_X, final_y, cat_features=cat_cols)

    final_model = CatBoostRegressor(
        loss_function="RMSE",
        eval_metric="RMSE",
        iterations=5000,
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

    # =========================
    # SAVE ARTIFACTS
    # =========================
    model_path = MODEL_DIR / f"catboost_spatial_{target_col}.cbm"
    meta_path = MODEL_DIR / f"catboost_spatial_{target_col}_meta.json"
    features_path = MODEL_DIR / f"model_{target_col}_features.json"
    features_path2 = MODEL_DIR / f"catboost_spatial_{target_col}_features.json"
    clusterer_path = MODEL_DIR / f"catboost_spatial_{target_col}_kmeans.joblib"
    importance_path = MODEL_DIR / f"catboost_spatial_{target_col}_feature_importance.csv"

    final_model.save_model(str(model_path))
    joblib.dump(kmeans, clusterer_path)
    imp_df.to_csv(importance_path, index=False, encoding="utf-8-sig")

    meta = {
        "excel_path": str(EXCEL_FILE),
        "sheet_name": SHEET_NAME,
        "target_col": target_col,
        "random_state": RANDOM_STATE,
        "use_log_target": USE_LOG_TARGET,
        "n_clusters": N_CLUSTERS,
        "test_cluster_fraction": TEST_CLUSTER_FRACTION,
        "n_splits_cv": N_SPLITS_CV,
        "feature_cols": feature_cols,
        "cat_cols": cat_cols,
        "pure_model_holdout_metrics": model_only_holdout_metrics,
        "hybrid_test_summary": hybrid_summary,
        "cv_average_metrics": cv_average_metrics,
        "train_rows": int(len(train_feat)),
        "test_rows": int(len(test_feat)),
        "train_lookup_source_rows": int(len(train_raw)),
        "test_rows_for_eval": int(len(test_raw)),
        "train_clusters": sorted(train_feat["cluster"].unique().tolist()),
        "test_clusters": sorted(test_feat["cluster"].unique().tolist()),
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    features_path.write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")
    features_path2.write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")

    # also write a second meta name for compatibility if needed
    meta_path2 = MODEL_DIR / f"model_{target_col}_meta.json"
    meta_path2.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

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
        "cv_average_metrics": cv_average_metrics,
        "pure_model_holdout_metrics": model_only_holdout_metrics,
        "hybrid_test_summary": hybrid_summary,
        "top_features": imp_df.head(20).to_dict(orient="records"),
        "train_rows": int(len(train_feat)),
        "test_rows": int(len(test_feat)),
    }


# =========================
# MAIN
# =========================
def main():
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(f"Không tìm thấy file Excel: {EXCEL_FILE}")

    df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME)
    df = canonicalize_columns(df)

    results = {}
    for target_col in TARGETS:
        results[target_col] = train_one_target(df, target_col)

    summary_path = MODEL_DIR / "training_summary_spatial_pro_v6.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã lưu tổng hợp kết quả tại: {summary_path}")


if __name__ == "__main__":
    main()