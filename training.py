import joblib
import matplotlib.pyplot as plt
import mysql.connector
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor


# ==============================================================================
# PARAMETER KONFIGURASI
# ==============================================================================

SEQUENTIAL_WINDOW = 10          # Fitur lag 1-10 menit
ROLLING_WINDOW_1H = 60          # Rolling mean 1 jam
BATAS_KEKERINGAN_KRITIS = 50.0  # Batas kelembapan tanah (%)
MAX_PREDICTION_MINUTES = 720.0  # Batas prediksi 12 jam (720 menit)

MODEL_OUTPUT_FILE = "xgboost_drought_model.joblib"
GRAFIK_OUTPUT_FILE = "grafik_evaluasi_xgboost.png"


# ==============================================================================
# KONFIGURASI DATABASE
# ==============================================================================

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "db_monitoring_nilam",
}


# ==============================================================================
# 1. MEMUAT DATA SENSOR DARI DATABASE
# ==============================================================================

def load_sensor_data():

    print(
        f"[1/7] Memuat data dari database "
        f"'{DB_CONFIG['database']}'..."
    )

    try:

        conn = mysql.connector.connect(**DB_CONFIG)

        query = """
            SELECT
                device_id,
                soil_moisture,
                temperature,
                humidity,
                timestamp
            FROM sensor_data
            ORDER BY device_id ASC, timestamp ASC;
        """

        df = pd.read_sql(query, conn)

        conn.close()

        df["timestamp"] = pd.to_datetime(df["timestamp"])

        print(
            f"[SUCCESS] Berhasil memuat "
            f"{len(df)} baris data sensor."
        )

        return df

    except Exception as e:

        print(f"[ERROR] Gagal membaca database: {e}")

        raise e


# ==============================================================================
# 2. FEATURE ENGINEERING
# ==============================================================================

def create_features(df):

    df_list = []

    for device_id, group in df.groupby("device_id"):

        group = (
            group
            .sort_values("timestamp")
            .reset_index(drop=True)
            .copy()
        )

        # ----------------------------------------------------------------------
        # Rolling Mean 1 Jam
        # ----------------------------------------------------------------------

        group["soil_roll_mean_1h"] = (
            group["soil_moisture"]
            .rolling(
                window=ROLLING_WINDOW_1H,
                min_periods=1
            )
            .mean()
        )

        group["temp_roll_mean_1h"] = (
            group["temperature"]
            .rolling(
                window=ROLLING_WINDOW_1H,
                min_periods=1
            )
            .mean()
        )

        group["hum_roll_mean_1h"] = (
            group["humidity"]
            .rolling(
                window=ROLLING_WINDOW_1H,
                min_periods=1
            )
            .mean()
        )

        # ----------------------------------------------------------------------
        # Perubahan kelembapan tanah
        # ----------------------------------------------------------------------

        group["soil_diff_10m"] = (
            group["soil_moisture"]
            .diff(10)
            .fillna(0.0)
        )

        group["soil_diff_30m"] = (
            group["soil_moisture"]
            .diff(30)
            .fillna(0.0)
        )

        # ----------------------------------------------------------------------
        # Fitur waktu
        # ----------------------------------------------------------------------

        group["hour"] = (
            group["timestamp"]
            .dt.hour
        )

        group["is_daytime"] = (
            (group["hour"] >= 6)
            &
            (group["hour"] <= 18)
        ).astype(int)

        # ----------------------------------------------------------------------
        # Sequential / Lag Features
        # ----------------------------------------------------------------------

        for lag in range(
            1,
            SEQUENTIAL_WINDOW + 1
        ):

            group[
                f"soil_lag_{lag:02d}"
            ] = (
                group["soil_moisture"]
                .shift(lag)
            )

        df_list.append(group)

    df_result = pd.concat(
        df_list,
        ignore_index=True
    )

    return (
        df_result
        .sort_values(
            ["device_id", "timestamp"]
        )
        .reset_index(drop=True)
    )


# ==============================================================================
# 3. MENENTUKAN FITUR YANG DIGUNAKAN MODEL
# ==============================================================================

def get_feature_columns():

    base_cols = [

        "soil_moisture",
        "humidity",
        "temperature",

        "soil_roll_mean_1h",
        "hum_roll_mean_1h",
        "temp_roll_mean_1h",

        "soil_diff_10m",
        "soil_diff_30m",

        "hour",
        "is_daytime",
    ]

    lag_cols = [

        f"soil_lag_{i:02d}"

        for i in range(
            1,
            SEQUENTIAL_WINDOW + 1
        )
    ]

    return base_cols + lag_cols


# ==============================================================================
# 4. PEMBENTUKAN TARGET
# ==============================================================================

def create_target(df):

    df_list = []

    for device_id, group in df.groupby("device_id"):

        group = (
            group
            .sort_values("timestamp")
            .reset_index(drop=True)
            .copy()
        )

        target = pd.Series(
            index=group.index,
            dtype=float
        )

        # ----------------------------------------------------------------------
        # Mencari indeks saat kelembapan tanah < 50%
        # ----------------------------------------------------------------------

        drought_indices = (
            group[
                group["soil_moisture"]
                < BATAS_KEKERINGAN_KRITIS
            ]
            .index
        )

        # ----------------------------------------------------------------------
        # Menghitung waktu menuju kekeringan
        # ----------------------------------------------------------------------

        for idx in group.index:

            # Jika kondisi sekarang sudah kering
            if (
                group.loc[idx, "soil_moisture"]
                < BATAS_KEKERINGAN_KRITIS
            ):

                target.loc[idx] = 0.0

                continue

            # Mencari kekeringan berikutnya
            future_droughts = (
                drought_indices[
                    drought_indices > idx
                ]
            )

            if not future_droughts.empty:

                next_drought_idx = (
                    future_droughts[0]
                )

                time_diff = (
                    group.loc[
                        next_drought_idx,
                        "timestamp"
                    ]
                    -
                    group.loc[
                        idx,
                        "timestamp"
                    ]
                ).total_seconds() / 60.0

                # Hanya menggunakan target <= 720 menit
                if (
                    time_diff
                    <= MAX_PREDICTION_MINUTES
                ):

                    target.loc[idx] = time_diff

                else:

                    target.loc[idx] = np.nan

            else:

                target.loc[idx] = np.nan

        group[
            "target_minutes_to_drought"
        ] = target

        df_list.append(group)

    return pd.concat(
        df_list,
        ignore_index=True
    )


# ==============================================================================
# 5. TRAINING DAN EVALUASI MODEL XGBOOST
# ==============================================================================

def train_model(
    X_train,
    X_test,
    y_train,
    y_test
):

    model = XGBRegressor(

        n_estimators=600,

        learning_rate=0.02,

        max_depth=6,

        subsample=0.8,

        colsample_bytree=0.8,

        random_state=42,
    )

    # Training model
    model.fit(
        X_train,
        y_train
    )

    # Prediksi data testing
    pred_minutes = (
        model.predict(X_test)
    )

    # Membatasi hasil prediksi 0-720 menit
    pred_minutes = np.clip(
        pred_minutes,
        0,
        MAX_PREDICTION_MINUTES
    )

    # --------------------------------------------------------------------------
    # Evaluasi
    # --------------------------------------------------------------------------

    mae = mean_absolute_error(
        y_test,
        pred_minutes
    )

    rmse = np.sqrt(
        mean_squared_error(
            y_test,
            pred_minutes
        )
    )

    r2 = r2_score(
        y_test,
        pred_minutes
    )

    print("\n" + "=" * 45)

    print(
        "       HASIL EVALUASI MODEL XGBOOST"
    )

    print("=" * 45)

    print(
        f"MAE  : {mae:.2f} Menit"
    )

    print(
        f"RMSE : {rmse:.2f} Menit"
    )

    print(
        f"R²   : {r2 * 100:.2f}%"
    )

    print("=" * 45 + "\n")

    return (
        model,
        pred_minutes,
        y_test,
        mae,
        rmse,
        r2
    )


# ==============================================================================
# 6. MEMBUAT GRAFIK AKTUAL VS PREDIKSI
# ==============================================================================

def plot_results(
    y_true,
    y_pred,
    mae,
    rmse,
    r2,
    filename
):

    plt.figure(
        figsize=(8, 7)
    )

    # --------------------------------------------------------------------------
    # Scatter aktual vs prediksi
    # --------------------------------------------------------------------------

    plt.scatter(
        y_true,
        y_pred,
        color="#3498db",
        alpha=0.5,
        edgecolors="none",
        s=30,
        label="Prediksi XGBoost"
    )

    # --------------------------------------------------------------------------
    # Garis ideal
    # --------------------------------------------------------------------------

    plt.plot(
        [
            0,
            MAX_PREDICTION_MINUTES
        ],
        [
            0,
            MAX_PREDICTION_MINUTES
        ],
        color="#c0392b",
        linestyle="--",
        linewidth=2,
        label="Garis Ideal"
    )

    # --------------------------------------------------------------------------
    # Menampilkan nilai evaluasi
    # --------------------------------------------------------------------------

    textstr = (
        f"MAE = {mae:.2f} menit\n"
        f"RMSE = {rmse:.2f} menit\n"
        f"R² = {r2 * 100:.2f}%"
    )

    props = dict(
        boxstyle="round,pad=0.6",
        facecolor="white",
        alpha=0.8,
        edgecolor="#cccccc"
    )

    plt.gca().text(
        0.05,
        0.93,
        textstr,
        transform=plt.gca().transAxes,
        fontsize=11,
        verticalalignment="top",
        bbox=props
    )

    # --------------------------------------------------------------------------
    # Judul dan label
    # --------------------------------------------------------------------------

    plt.title(
        "Scatter Plot Nilai Aktual vs Prediksi XGBoost\n"
        "(Batas Prediksi 720 Menit / 12 Jam)",
        fontsize=12,
        fontweight="bold"
    )

    plt.xlabel(
        "Waktu Aktual Menuju Kekeringan (Menit)",
        fontsize=11
    )

    plt.ylabel(
        "Prediksi Waktu Menuju Kekeringan (Menit)",
        fontsize=11
    )

    # --------------------------------------------------------------------------
    # Skala grafik
    # --------------------------------------------------------------------------

    plt.xlim(
        0,
        MAX_PREDICTION_MINUTES
    )

    plt.ylim(
        0,
        MAX_PREDICTION_MINUTES
    )

    plt.xticks(
        np.arange(
            0,
            MAX_PREDICTION_MINUTES + 1,
            60
        )
    )

    plt.yticks(
        np.arange(
            0,
            MAX_PREDICTION_MINUTES + 1,
            60
        )
    )

    plt.grid(
        True,
        linestyle=":",
        alpha=0.6
    )

    plt.legend(
        loc="lower right"
    )

    plt.tight_layout()

    plt.savefig(
        filename,
        dpi=300,
        bbox_inches="tight"
    )

    plt.close()

    print(
        f"[INFO] Grafik evaluasi disimpan "
        f"ke '{filename}'"
    )
# ==============================================================================
# 7. PROGRAM UTAMA
# ==============================================================================

if __name__ == "__main__":
    df_raw = load_sensor_data()
    # --------------------------------------------------------------------------
    # Feature engineering
    # --------------------------------------------------------------------------

    df_feat = create_features(
        df_raw
    )

    # --------------------------------------------------------------------------
    # Membuat target
    # --------------------------------------------------------------------------

    df_target = create_target(
        df_feat
    )

    # --------------------------------------------------------------------------
    # Mendapatkan daftar fitur
    # --------------------------------------------------------------------------

    fitur_cols = get_feature_columns()

    # --------------------------------------------------------------------------
    # Menghapus data yang tidak lengkap
    # --------------------------------------------------------------------------

    df_model = (
        df_target
        .dropna(
            subset=
            fitur_cols
            +
            ["target_minutes_to_drought"]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------------------------
    # Hanya menggunakan data dengan perubahan kelembapan tanah menurun
    # --------------------------------------------------------------------------

    df_model = (
        df_model[
            df_model["soil_diff_10m"] < 0
        ]
        .reset_index(drop=True)
    )
    # --------------------------------------------------------------------------
    # Memisahkan X dan y
    # --------------------------------------------------------------------------

    X = df_model[
        fitur_cols
    ]

    y = df_model[
        "target_minutes_to_drought"
    ]

    # --------------------------------------------------------------------------
    # Membagi data training dan testing
    # --------------------------------------------------------------------------

    X_train, X_test, y_train, y_test = (
        train_test_split(
            X,
            y,
            test_size=0.2,
            random_state=42,
            shuffle=True
        )
    )

    print("\n" + "=" * 60)

    print(
        "PEMBAGIAN DATA"
    )

    print("=" * 60)

    print(
        f"Data training : {len(X_train)}"
    )

    print(
        f"Data testing  : {len(X_test)}"
    )

    # --------------------------------------------------------------------------
    # Training XGBoost
    # --------------------------------------------------------------------------

    (
        model,
        pred_min,
        y_true_min,
        mae,
        rmse,
        r2
    ) = train_model(
        X_train,
        X_test,
        y_train,
        y_test
    )

    # --------------------------------------------------------------------------
    # Menyimpan model dan daftar fitur
    # --------------------------------------------------------------------------

    payload_to_save = {

        "model": model,

        "features": fitur_cols
    }

    joblib.dump(
        payload_to_save,
        MODEL_OUTPUT_FILE
    )

    print(
        f"[SUCCESS] Model disimpan sebagai "
        f"'{MODEL_OUTPUT_FILE}'"
    )

    # --------------------------------------------------------------------------
    # Membuat grafik evaluasi
    # --------------------------------------------------------------------------

    plot_results(
        y_true_min,
        pred_min,
        mae,
        rmse,
        r2,
        GRAFIK_OUTPUT_FILE
    )

    print("\n" + "=" * 60)

    print(
        "PROSES SELESAI"
    )

    print("=" * 60)