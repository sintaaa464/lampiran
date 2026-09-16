<?php
// ==============================================================================
// CONFIG DOKUMEN & PARAMETER PERANGKAT
// ==============================================================================
$fastapi_base_url = "http://localhost:8000";
$device_id = isset($_GET['device_id']) ? intval($_GET['device_id']) : 1;
?>

<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistem Monitoring Tanaman Nilam</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Chart.js CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <!-- FontAwesome CDN -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body {
            font-family: Arial, Helvetica, sans-serif;
            background-color: #f8fafc;
        }
    </style>
</head>
<body class="flex justify-center items-center min-h-screen py-8">

    <!-- Container Utama Dashboard -->
    <div class="w-full max-w-4xl bg-white border border-gray-400 rounded-sm shadow-md p-6">
        
        <!-- Judul Utama -->
        <h1 class="text-2xl font-bold text-center text-black mb-6">
            Sistem Monitoring Tanaman Nilam
        </h1>

        <!-- Header Dashboard -->
        <div class="flex justify-center mb-4">
            <span class="bg-emerald-100 text-emerald-800 px-6 py-2 rounded-md font-bold text-sm tracking-wide">
                Dashboard Data Sensor
            </span>
        </div>

        <!-- Form Pilih Perangkat -->
        <form method="GET" action="" class="flex justify-center items-center space-x-2 mb-6">
            <label class="font-bold text-sm text-black">Pilih Perangkat :</label>
            <select name="device_id" onchange="this.form.submit()" class="border border-gray-400 rounded px-3 py-1 text-sm font-semibold text-gray-800 bg-white focus:outline-none">
                <option value="1" <?= $device_id == 1 ? 'selected' : ''; ?>>NODEESP32-1</option>
                <option value="2" <?= $device_id == 2 ? 'selected' : ''; ?>>NODEESP32-2</option>
            </select>
        </form>

        <!-- 3 Card Indikator Sensor -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
            
            <!-- Kelembapan Tanah -->
            <div class="border border-gray-300 rounded-sm overflow-hidden shadow-sm">
                <div class="bg-blue-100 text-blue-800 text-center font-bold text-sm py-2 border-b border-gray-300">
                    Kelembapan Tanah
                </div>
                <div class="p-6 flex justify-center items-center space-x-3 bg-white">
                    <div class="border-2 border-black rounded-full p-2 flex items-center justify-center w-10 h-10">
                        <i class="fa-solid fa-droplet text-black text-lg"></i>
                    </div>
                    <span class="text-2xl font-bold text-black">
                        <span id="val-soil">0.00</span> <span class="text-sm font-normal">%</span>
                    </span>
                </div>
            </div>

            <!-- Kelembapan Udara -->
            <div class="border border-gray-300 rounded-sm overflow-hidden shadow-sm">
                <div class="bg-emerald-100 text-emerald-800 text-center font-bold text-sm py-2 border-b border-gray-300">
                    Kelembapan Udara
                </div>
                <div class="p-6 flex justify-center items-center space-x-3 bg-white">
                    <div class="text-emerald-500 text-3xl">
                        <i class="fa-solid fa-water"></i>
                    </div>
                    <span class="text-2xl font-bold text-black">
                        <span id="val-humidity">0.0</span> <span class="text-sm font-normal">%</span>
                    </span>
                </div>
            </div>

            <!-- Suhu Udara -->
            <div class="border border-gray-300 rounded-sm overflow-hidden shadow-sm">
                <div class="bg-gray-100 text-gray-800 text-center font-bold text-sm py-2 border-b border-gray-300">
                    Suhu Udara
                </div>
                <div class="p-6 flex justify-center items-center space-x-3 bg-white">
                    <div class="text-amber-500 text-3xl">
                        <i class="fa-solid fa-temperature-high"></i>
                    </div>
                    <span class="text-2xl font-bold text-black">
                        <span id="val-temp">0.0</span> <span class="text-sm font-normal">°C</span>
                    </span>
                </div>
            </div>

        </div>

        <!-- Terakhir Update -->
        <div class="flex justify-center mb-8">
            <div class="bg-emerald-50 text-emerald-800 border border-emerald-200 px-4 py-1.5 rounded-md text-xs font-bold space-x-2 flex items-center">
                <span id="pulse-dot" class="w-2 h-2 rounded-full bg-emerald-500 mr-1 animate-ping"></span>
                <span>Terakhir Update :</span>
                <span id="val-last-update">-</span>
            </div>
        </div>

        <!-- Section Grafik -->
        <div class="border border-gray-200 rounded-md p-4 bg-gray-50/50">
            <h2 class="text-center font-bold text-base text-black mb-4">Grafik Riwayat Sensor</h2>
            <div class="relative h-72 w-full">
                <canvas id="riwayatChart"></canvas>
            </div>
        </div>

    </div>

    <!-- Script AJAX Real-Time & Chart.js -->
    <script>
        const FASTAPI_BASE_URL = "<?= $fastapi_base_url; ?>";
        const DEVICE_ID = <?= $device_id; ?>;

        // 1. Inisialisasi Chart.js Kosong
        const ctx = document.getElementById('riwayatChart').getContext('2d');
        const riwayatChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'Kelembapan Tanah (%)',
                        data: [],
                        borderColor: '#9a3412', // Cokelat
                        backgroundColor: '#9a3412',
                        borderWidth: 2,
                        pointRadius: 3,
                        tension: 0.1
                    },
                    {
                        label: 'Kelembapan Udara (%)',
                        data: [],
                        borderColor: '#3b82f6', // Biru
                        backgroundColor: '#3b82f6',
                        borderWidth: 2,
                        pointRadius: 3,
                        tension: 0.1
                    },
                    {
                        label: 'Suhu Udara (°C)',
                        data: [],
                        borderColor: '#ef4444', // Merah
                        backgroundColor: '#ef4444',
                        borderWidth: 2,
                        pointRadius: 3,
                        tension: 0.1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'top',
                        labels: {
                            boxWidth: 20,
                            font: { size: 11, weight: 'bold' }
                        }
                    }
                },
                scales: {
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { stepSize: 10, font: { size: 10 } },
                        title: { display: true, text: 'Nilai Sensor', font: { size: 11 } }
                    },
                    x: {
                        ticks: { font: { size: 10 } },
                        title: { display: true, text: 'Waktu', font: { size: 11 } }
                    }
                }
            }
        });

        // Helper Format Tanggal
        function formatTanggal(timestampStr) {
            if (!timestampStr) return "-";
            const date = new Date(timestampStr.replace(' ', 'T'));
            if (isNaN(date)) return timestampStr;
            
            const dd = String(date.getDate()).padStart(2, '0');
            const mm = String(date.getMonth() + 1).padStart(2, '0');
            const yyyy = date.getFullYear();
            const hh = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            const ss = String(date.getSeconds()).padStart(2, '0');
            
            return `${dd}/${mm}/${yyyy}  ${hh}.${min}.${ss}`;
        }

        // 2. Fungsi AJAX Fetch Data Terbaru
        async function fetchRealtimeData() {
            try {
                // Fetch Data /sensor
                const resLatest = await fetch(`${FASTAPI_BASE_URL}/sensor?device_id=${DEVICE_ID}`);
                let latestData = await resLatest.json();

                // Unwrap Data jika berbentuk Array/Object wrapper
                if (Array.isArray(latestData) && latestData.length > 0) {
                    latestData = latestData[0];
                } else if (latestData.data) {
                    latestData = Array.isArray(latestData.data) ? latestData.data[0] : latestData.data;
                }

                if (latestData && latestData.soil_moisture !== undefined) {
                    document.getElementById('val-soil').innerText = parseFloat(latestData.soil_moisture).toFixed(2);
                    document.getElementById('val-humidity').innerText = parseFloat(latestData.humidity).toFixed(1);
                    document.getElementById('val-temp').innerText = parseFloat(latestData.temperature).toFixed(1);
                    document.getElementById('val-last-update').innerText = formatTanggal(latestData.timestamp);
                }

                // Fetch Data /sensor/history untuk Grafik
                const resHist = await fetch(`${FASTAPI_BASE_URL}/sensor/history?device_id=${DEVICE_ID}`);
                let histData = await resHist.json();

                if (histData.data) histData = histData.data;

                if (Array.isArray(histData) && histData.length > 0) {
                    const labels = [];
                    const soilData = [];
                    const humData = [];
                    const tempData = [];

                    histData.forEach(row => {
                        if (row.timestamp) {
                            const timePart = row.timestamp.split(' ')[1] || "00:00:00";
                            labels.push(timePart.substring(0, 5));
                            soilData.push(row.soil_moisture || 0);
                            humData.push(row.humidity || 0);
                            tempData.push(row.temperature || 0);
                        }
                    });

                    // Update Data Grafik secara Instan
                    riwayatChart.data.labels = labels;
                    riwayatChart.data.datasets[0].data = soilData;
                    riwayatChart.data.datasets[1].data = humData;
                    riwayatChart.data.datasets[2].data = tempData;
                    riwayatChart.update('none'); // Update tanpa animasi ulang agar smooth
                }

            } catch (err) {
                console.error("Gagal menarik data via AJAX:", err);
            }
        }

        // Jalankan saat pertama kali dibuka
        fetchRealtimeData();

        // 3. Polling Otomatis Setiap 3 Detik (3000 ms)
        setInterval(fetchRealtimeData, 3000);
    </script>
</body>
</html>