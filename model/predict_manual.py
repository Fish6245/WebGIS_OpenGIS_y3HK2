# predict_manual.py

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

# =====================================================
# NHẬP THÔNG TIN
# =====================================================

ROAD_NAME = "Trương Định"
DISTRICT = "Quận 1"

# nếu có Phường thì điền thêm
WARD = None
# ví dụ:
# WARD = "Phường Bến Thành"

# =====================================================
# CONFIG
# =====================================================

EXCEL_FILE = "./model/HCM.xlsx"
SHEET_NAME = "road_giadat_tphcm"

MODEL_DIR = Path("./model/artifacts")

# =====================================================
# LOAD MODEL
# =====================================================

model_2019 = CatBoostRegressor()
model_2019.load_model(
    str(MODEL_DIR / "catboost_spatial_GiaDat2019.cbm")
)

model_2025 = CatBoostRegressor()
model_2025.load_model(
    str(MODEL_DIR / "catboost_spatial_GiaDat2025.cbm")
)

features_2019 = json.loads(
    (
        MODEL_DIR /
        "catboost_spatial_GiaDat2019_features.json"
    ).read_text(encoding="utf-8")
)

features_2025 = json.loads(
    (
        MODEL_DIR /
        "catboost_spatial_GiaDat2025_features.json"
    ).read_text(encoding="utf-8")
)


# =====================================================
# HELPERS
# =====================================================

def normalize_text(value):

    if pd.isna(value):
        return ""

    s = str(value)

    s = s.replace("\n", " ")
    s = s.replace("\r", " ")

    s = " ".join(s.split())

    return s.strip()


def canonicalize_columns(df):

    df = df.copy()

    if "Phuong" not in df.columns and "Phường" in df.columns:
        df["Phuong"] = df["Phường"]

    if "X" not in df.columns and "Longitude" in df.columns:
        df["X"] = df["Longitude"]

    if "Y" not in df.columns and "Latitude" in df.columns:
        df["Y"] = df["Latitude"]

    for col in [
        "road_count",
        "place_count",
        "nearest_road_distance",
        "nearest_place_distance"
    ]:

        if col not in df.columns:
            df[col] = np.nan

    for col in [
        "ThanhPho",
        "TinhThanh",
        "QuocGia"
    ]:

        if col not in df.columns:
            df[col] = ""

    return df


def build_features(df):

    df = canonicalize_columns(df).copy()

    text_cols = [
        "TenDuong",
        "QuanHuyen",
        "Phuong",
        "ThanhPho",
        "TinhThanh",
        "QuocGia"
    ]

    for col in text_cols:

        df[col] = (
            df[col]
            .fillna("")
            .map(normalize_text)
        )

    numeric_cols = [
        "X",
        "Y",
        "road_count",
        "place_count",
        "nearest_road_distance",
        "nearest_place_distance"
    ]

    for col in numeric_cols:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # =================================================
    # DERIVED TEXT
    # =================================================

    df["road_key"] = (
        df["TenDuong"]
        + "|"
        + df["QuanHuyen"]
        + "|"
        + df["Phuong"]
    )

    df["road_district_key"] = (
        df["TenDuong"]
        + "|"
        + df["QuanHuyen"]
    )

    df["road_ward_key"] = (
        df["TenDuong"]
        + "|"
        + df["Phuong"]
    )

    df["district_ward_key"] = (
        df["QuanHuyen"]
        + "|"
        + df["Phuong"]
    )

    df["road_full_key"] = (
        df["TenDuong"]
        + "|"
        + df["QuanHuyen"]
        + "|"
        + df["Phuong"]
        + "|"
        + df["ThanhPho"]
        + "|"
        + df["TinhThanh"]
    )

    def first_token(x):

        x = normalize_text(x)

        if x == "":
            return ""

        return x.split()[0]


    def last_token(x):

        x = normalize_text(x)

        if x == "":
            return ""

        return x.split()[-1]


    df["TenDuong_prefix"] = (
        df["TenDuong"]
        .map(first_token)
    )

    df["TenDuong_suffix"] = (
        df["TenDuong"]
        .map(last_token)
    )

    # =================================================
    # DERIVED NUMERIC
    # =================================================

    df["road_name_len"] = (
        df["TenDuong"]
        .astype(str)
        .str.len()
    )

    df["road_name_word_count"] = (
        df["TenDuong"]
        .astype(str)
        .str.split()
        .str.len()
    )

    df["district_len"] = (
        df["QuanHuyen"]
        .astype(str)
        .str.len()
    )

    df["district_word_count"] = (
        df["QuanHuyen"]
        .astype(str)
        .str.split()
        .str.len()
    )

    df["ward_len"] = (
        df["Phuong"]
        .astype(str)
        .str.len()
    )

    df["ward_word_count"] = (
        df["Phuong"]
        .astype(str)
        .str.split()
        .str.len()
    )

    df["road_key_len"] = (
        df["road_key"]
        .astype(str)
        .str.len()
    )

    df["road_key_word_count"] = (
        df["road_key"]
        .astype(str)
        .str.split()
        .str.len()
    )

    df["x_round_3"] = (
        df["X"]
        .round(3)
    )

    df["y_round_3"] = (
        df["Y"]
        .round(3)
    )

    # tránh lỗi CatBoost cat_feature=nan

    for col in df.columns:

        if (
            df[col].dtype == object
            or str(df[col].dtype) == "category"
        ):

            df[col] = (
                df[col]
                .fillna("")
                .astype(str)
            )

    return df


# =====================================================
# LOAD EXCEL
# =====================================================

df = pd.read_excel(
    EXCEL_FILE,
    sheet_name=SHEET_NAME
)

df = canonicalize_columns(df)

for col in df.columns:

    if df[col].dtype == object:

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

# =====================================================
# TÌM ĐƯỜNG
# =====================================================

mask = (
    df["TenDuong"]
    .str.lower()
    .str.strip()
    ==
    ROAD_NAME.lower().strip()
)

mask &= (
    df["QuanHuyen"]
    .str.lower()
    .str.strip()
    ==
    DISTRICT.lower().strip()
)

if WARD is not None and "Phuong" in df.columns:

    mask &= (
        df["Phuong"]
        .str.lower()
        .str.strip()
        ==
        WARD.lower().strip()
    )

matches = df[mask]

if len(matches) == 0:

    print("Không tìm thấy đường.")

    quit()

print(f"Tìm thấy {len(matches)} dòng.")

row = matches.iloc[0].copy()

# =====================================================
# BUILD FEATURE
# =====================================================

row_df = pd.DataFrame([row])

row_df = build_features(row_df)

# =====================================================
# TẠO INPUT
# =====================================================

X2019 = (
    row_df
    .reindex(
        columns=features_2019,
        fill_value=np.nan
    )
)

X2025 = (
    row_df
    .reindex(
        columns=features_2025,
        fill_value=np.nan
    )
)

for X in [X2019, X2025]:

    for col in X.columns:

        if (
            X[col].dtype == object
            or str(X[col].dtype) == "category"
        ):

            X[col] = (
                X[col]
                .fillna("")
                .astype(str)
            )

# =====================================================
# PREDICT
# =====================================================

pred2019_log = model_2019.predict(X2019)

pred2025_log = model_2025.predict(X2025)

pred2019 = float(
    np.expm1(pred2019_log[0])
)

pred2025 = float(
    np.expm1(pred2025_log[0])
)

# =====================================================
# ĐỘ CHÍNH XÁC
# =====================================================

acc2019 = None
acc2025 = None

if (
    "GiaDat2019" in row.index
    and
    pd.notna(row["GiaDat2019"])
):

    actual2019 = float(row["GiaDat2019"])

    error2019 = abs(
        pred2019
        -
        actual2019
    )

    acc2019 = max(
        0,
        100
        -
        error2019 / actual2019 * 100
    )

if (
    "GiaDat2025" in row.index
    and
    pd.notna(row["GiaDat2025"])
):

    actual2025 = float(row["GiaDat2025"])

    error2025 = abs(
        pred2025
        -
        actual2025
    )

    acc2025 = max(
        0,
        100
        -
        error2025 / actual2025 * 100
    )

# =====================================================
# HIỂN THỊ
# =====================================================

print()

print("=" * 60)
print("THÔNG TIN ĐƯỜNG")
print("=" * 60)

for col in [
    "TenDuong",
    "QuanHuyen",
    "Phuong",
    "TuDiem",
    "DenDiem",
    "X",
    "Y"
]:

    if col in row.index:

        print(
            f"{col}: {row[col]}"
        )

print()

print("=" * 60)
print("KẾT QUẢ DỰ ĐOÁN")
print("=" * 60)

print(
    f"Giá đất 2019 dự đoán : "
    f"{pred2019:,.0f}"
)

if (
    "GiaDat2019" in row.index
    and
    pd.notna(row["GiaDat2019"])
):

    print(
        f"Giá đất 2019 thực tế : "
        f"{float(row['GiaDat2019']):,.0f}"
    )

    print(
        f"Độ chính xác 2019 : "
        f"{acc2019:.2f}%"
    )

print()

print(
    f"Giá đất 2025 dự đoán : "
    f"{pred2025:,.0f}"
)

if (
    "GiaDat2025" in row.index
    and
    pd.notna(row["GiaDat2025"])
):

    print(
        f"Giá đất 2025 thực tế : "
        f"{float(row['GiaDat2025']):,.0f}"
    )

    print(
        f"Độ chính xác 2025 : "
        f"{acc2025:.2f}%"
    )

print("=" * 60)
