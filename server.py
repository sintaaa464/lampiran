from datetime import datetime
import os
import time

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import joblib
import mysql.connector
from mysql.connector import pooling
import numpy as np
import pandas as pd
from pydantic import BaseModel
import requests

# ==============================================================================
# 1. KONFIGURASI UTAMA & LOAD MODEL
# ==============================================================================
SEQUENTIAL_WINDOW = 10  # Fitur lag 1-10 menit
ROLLING_WINDOW_1H = 60  # Rolling mean 1 jam
BATAS_KEKERINGAN_KRITIS = 50.0  # Threshold kekeringan tanah (%)
MAX_PREDICTION_MINUTES = 720.0  # Maksimal estimasi 12 jam (720 menit)
INTERVAL_PREDIKSI_MENIT = 30  # AI berjalan tiap 30 menit

MODEL_FILE = "xgboost_drought_model.joblib"

# Load Model & Daftar Fitur
try:
    payload = joblib.load(MODEL_FILE)
    if isinstance(payload, dict):
        model = payload.get("model")
        kolom_fitur = payload.get("features", [])
    else:
        model = payload
        # Backup susunan kolom jika tidak dalam format dictionary
        kolom_fitur = [
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
        ] + [f"soil_lag_{i:02d}" for i in range(1, SEQUENTIAL_WINDOW + 1)]

    print(
        f"[INFO] Model XGBoost & {len(kolom_fitur)} fitur berhasil dimuat."
    )
except Exception as e:
    print(f"[ERROR] Gagal memuat file model ({MODEL_FILE}): {e}")
    model, kolom_fitur = None, []

# Kredensial Bot Telegram
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "8821131147:AAEIyoelGcCj20LWILWSR1XgM7PlTzijhWE"
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8734016764")

# ==============================================================================
# 2. INISIALISASI FASTAPI & DATABASE
# ==============================================================================
app = FastAPI(title="API Monitoring & Early Warning Nilam")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pool Database MySQL
db_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="nilam_pool",
    pool_size=10,
    pool_reset_session=True,
    host="localhost",
    user="root",
    password="",
    database="db_monitoring_nilam",
)


def get_db():
    return db_pool.get_connection()


# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================
def format_waktu(est_menit):
    total = max(0, int(round(est_menit)))
    j, m = total // 60, total % 60
    if j > 0 and m > 0:
        return f"{j} jam {m} menit ke depan"
    elif j > 0:
        return f"{j} jam ke depan"
    return f"{m} menit ke depan"


def kirim_telegram(device_id, soil, temp, hum, timestamp, est_menit):
    if est_menit >= MAX_PREDICTION_MINUTES and soil >= BATAS_KEKERINGAN_KRITIS:
        return

    durasi_str = format_waktu(est_menit)
    if soil < BATAS_KEKERINGAN_KRITIS:
        status_txt = f"🚨 STATUS: Kelembapan tanah SUDAH KRITIS (<{BATAS_KEKERINGAN_KRITIS:.0f}%)\n"
        aksi_txt = "Tanah dalam kondisi sangat kering. SEGERA PENYIRAMAN!"
    else:
        status_txt = f"⏳ PREDIKSI: Diprediksi KEKERINGAN dalam {durasi_str}\n"
        aksi_txt = "Mohon siapkan penyiraman sebelum tanaman dehidrasi!"

    pesan = (
        f"🚨 PERINGATAN DINI KEKERINGAN NILAM 🚨\n\n"
        f"📍 Perangkat           : Device {device_id}\n"
        f"🌱 Kelembapan Tanah   : {soil}%\n"
        f"🌡️ Suhu / Kelembapan  : {temp}°C / {hum}%\n"
        f"⏱️ Waktu Evaluasi     : {timestamp.strftime('%d-%m-%Y %H:%M:%S')} WITA\n\n"
        f"{status_txt}\n💦 TINDAKAN:\n{aksi_txt}"
    )

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": int(TELEGRAM_CHAT_ID.strip()),
                "text": pesan,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


def ekstrasi_fitur(history):
    """Fungsi internal untuk ekstraksi fitur dari data history MySQL"""
    # Butuh minimal ROLLING_WINDOW_1H (60 data) agar rolling mean 1 jam akurat
    if len(history) < ROLLING_WINDOW_1H:
        return None

    # Urutkan dari data lama ke baru
    chrono = list(reversed(history))
    latest = chrono[-1]

    soil_series = pd.Series([float(h["soil_moisture"]) for h in chrono])
    temp_series = pd.Series([float(h["temperature"]) for h in chrono])
    hum_series = pd.Series([float(h["humidity"]) for h in chrono])
    ts = latest["timestamp"]

    # 1. Fitur Dasar & Rolling Mean 1 Jam
    data = {
        "soil_moisture": float(latest["soil_moisture"]),
        "humidity": float(latest["humidity"]),
        "temperature": float(latest["temperature"]),
        "soil_roll_mean_1h": float(
            soil_series.iloc[-ROLLING_WINDOW_1H:].mean()
        ),
        "hum_roll_mean_1h": float(hum_series.iloc[-ROLLING_WINDOW_1H:].mean()),
        "temp_roll_mean_1h": float(
            temp_series.iloc[-ROLLING_WINDOW_1H:].mean()
        ),
    }

    # 2. Perubahan Kelembapan Tanah (diff 10m & 30m)
    data["soil_diff_10m"] = (
        float(soil_series.iloc[-1] - soil_series.iloc[-11])
        if len(soil_series) >= 11
        else 0.0
    )
    data["soil_diff_30m"] = (
        float(soil_series.iloc[-1] - soil_series.iloc[-31])
        if len(soil_series) >= 31
        else 0.0
    )

    # 3. Fitur Waktu
    data["hour"] = ts.hour
    data["is_daytime"] = 1 if (6 <= ts.hour <= 18) else 0

    # 4. Sequential / Lag Features (Lag 1-10)
    for lag in range(1, SEQUENTIAL_WINDOW + 1):
        data[f"soil_lag_{lag:02d}"] = float(soil_series.iloc[-(lag + 1)])

    return pd.DataFrame([data])[kolom_fitur], latest


# ==============================================================================
# 4. SCHEDULER INFERENSI AI
# ==============================================================================
def tugas_prediksi_terjadwal():
    if not model:
        return

    db = None
    cursor = None
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT DISTINCT device_id FROM sensor_data")
        devices = cursor.fetchall()

        for dev in devices:
            dev_id = dev["device_id"]

            # Ambil 61 data terakhir dari MySQL untuk menghitung Rolling 60 & Diff 30
            cursor.execute(
                """
                SELECT sensor_id, soil_moisture, temperature, humidity, timestamp 
                FROM sensor_data 
                WHERE device_id = %s 
                ORDER BY timestamp DESC LIMIT 61
                """,
                (dev_id,),
            )
            history = cursor.fetchall()

            res = ekstrasi_fitur(history)
            if res is None:
                continue

            df_pred, latest = res

            # Prediksi XGBoost langsung (Tanpa expm1 karena target dilatih langsung)
            pred_raw = float(model.predict(df_pred)[0])
            est_menit = float(np.clip(pred_raw, 0, MAX_PREDICTION_MINUTES))

            # Simpan log prediksi ke DB
            cursor.execute(
                """
                INSERT INTO prediction_log (sensor_id, estimated_minutes, prediction_time) 
                VALUES (%s, %s, %s)
                """,
                (
                    latest["sensor_id"],
                    int(round(est_menit)),
                    latest["timestamp"],
                ),
            )
            db.commit()

            # Kirim Telegram
            kirim_telegram(
                dev_id,
                float(latest["soil_moisture"]),
                float(latest["temperature"]),
                float(latest["humidity"]),
                latest["timestamp"],
                est_menit,
            )

            print(
                f"[AI SUCCESS] Device {dev_id} | Estimasi Kering: {format_waktu(est_menit)}"
            )

    except Exception as e:
        if db:
            db.rollback()
        print(f"[AI ERROR] {e}")
    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


# Jalankan APScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(
    tugas_prediksi_terjadwal, "interval", minutes=INTERVAL_PREDIKSI_MENIT
)
scheduler.start()


# ==============================================================================
# 5. ENDPOINT FASTAPI
# ==============================================================================
class SensorRequest(BaseModel):
    device_id: int = 1
    soil_moisture: float
    humidity: float
    temperature: float


@app.post("/sensor")
def post_sensor(data: SensorRequest):
    db = get_db()
    cursor = db.cursor()
    try:
        now = datetime.now()
        cursor.execute(
            """
            INSERT INTO sensor_data (device_id, soil_moisture, humidity, temperature, timestamp)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                data.device_id,
                data.soil_moisture,
                data.humidity,
                data.temperature,
                now,
            ),
        )
        db.commit()
        sid = cursor.lastrowid
        return {"status": "success", "sensor_id": sid}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()
        db.close()


@app.get("/sensor")
def get_sensor(device_id: int = Query(1)):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT s.*, p.estimated_minutes 
            FROM sensor_data s 
            LEFT JOIN prediction_log p ON s.sensor_id = p.sensor_id 
            WHERE s.device_id = %s 
            ORDER BY s.sensor_id DESC LIMIT 1
            """,
            (device_id,),
        )
        data = cursor.fetchone()

        if data:
            if data.get("timestamp"):
                data["timestamp"] = data["timestamp"].isoformat()

            est_min = data.get("estimated_minutes")
            data["estimasi_formatted"] = (
                format_waktu(est_min)
                if est_min is not None
                else "Menunggu jadwal prediksi..."
            )

        return data
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()
        db.close()


@app.get("/sensor/history")
def get_sensor_history(device_id: int = Query(1)):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT 
                DATE(timestamp) AS tanggal,
                HOUR(timestamp) AS jam_angka,
                ROUND(AVG(soil_moisture), 2) AS soil_moisture,
                ROUND(AVG(humidity), 1) AS humidity,
                ROUND(AVG(temperature), 1) AS temperature
            FROM sensor_data
            WHERE device_id = %s AND DATE(timestamp) = CURDATE()
            GROUP BY DATE(timestamp), HOUR(timestamp)
            ORDER BY jam_angka ASC
            """,
            (device_id,),
        )
        data = cursor.fetchall()

        for row in data:
            jam_str = str(row["jam_angka"]).zfill(2)
            tgl_str = (
                row["tanggal"].strftime("%Y-%m-%d")
                if hasattr(row["tanggal"], "strftime")
                else str(row["tanggal"])
            )
            row["timestamp"] = f"{tgl_str} {jam_str}:00:00"
            del row["tanggal"]

        return data
    except Exception:
        return []
    finally:
        cursor.close()
        db.close()


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "FastAPI Single Code (XGBoost 10-Lag & Feature Diff)",
    }