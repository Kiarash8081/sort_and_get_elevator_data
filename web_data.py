from flask import Flask, request, render_template_string, session, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
import pandas as pd
import os
from datetime import datetime, timedelta
import tempfile
import hashlib
import threading
import uuid
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.secret_key = os.environ.get('SECRET_KEY', 'elevator-analytics-secret')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

CACHE = {}
CACHE_EXPIRE = {}
CACHE_TIMEOUT = 259200
JOBS = {}

ALLOWED_EXTENSIONS = {'csv'}

def get_file_hash(file_data):
    return hashlib.md5(file_data).hexdigest()

def get_cached_result(file_hash):
    if file_hash in CACHE:
        if file_hash in CACHE_EXPIRE and datetime.now() < CACHE_EXPIRE[file_hash]:
            return CACHE[file_hash]
        else:
            del CACHE[file_hash]
            if file_hash in CACHE_EXPIRE:
                del CACHE_EXPIRE[file_hash]
    return None

def set_cache_result(file_hash, result):
    CACHE[file_hash] = result
    CACHE_EXPIRE[file_hash] = datetime.now() + timedelta(seconds=CACHE_TIMEOUT)

def make_json_safe(result):
    daily_df = result['daily_df'].copy()
    if 'day' in daily_df.columns:
        daily_df['day'] = daily_df['day'].astype(str)
    
    return {
        'total_days': int(result['total_days']),
        'total_calls': float(result['total_calls']),
        'total_incalls': float(result['total_incalls']),
        'total_upcalls': float(result['total_upcalls']),
        'total_downcalls': float(result['total_downcalls']),
        'avg_wait_time': float(result['avg_wait_time']),
        'avg_travel_time': float(result['avg_travel_time']),
        'min_date': str(result['min_date']) if result['min_date'] is not None else None,
        'max_date': str(result['max_date']) if result['max_date'] is not None else None,
        'daily_df': daily_df.to_dict('records'),
        'floor_stats': result['floor_stats'].to_dict(),
        'elevator_stats': result['elevator_stats'].to_dict(),
        'call_type_stats': result['call_type_stats'].to_dict()
    }

def restore_from_json(data):
    daily_df = pd.DataFrame(data['daily_df'])
    
    if 'day' in daily_df.columns:
        daily_df['day'] = pd.to_datetime(daily_df['day']).dt.date
    
    return {
        'total_days': data['total_days'],
        'total_calls': data['total_calls'],
        'total_incalls': data['total_incalls'],
        'total_upcalls': data['total_upcalls'],
        'total_downcalls': data['total_downcalls'],
        'avg_wait_time': data['avg_wait_time'],
        'avg_travel_time': data['avg_travel_time'],
        'min_date': datetime.strptime(data['min_date'], '%Y-%m-%d').date() if data['min_date'] and data['min_date'] != 'NaT' and data['min_date'] != 'None' else None,
        'max_date': datetime.strptime(data['max_date'], '%Y-%m-%d').date() if data['max_date'] and data['max_date'] != 'NaT' and data['max_date'] != 'None' else None,
        'daily_df': daily_df,
        'floor_stats': pd.Series(data['floor_stats']),
        'elevator_stats': pd.Series(data['elevator_stats']),
        'call_type_stats': pd.Series(data['call_type_stats'])
    }

# ================ HTML صفحه اصلی ================
INDEX_HTML = '''
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>تحلیل آمار تماس‌ها و سفرهای آسانسور</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #e8e8e8;
            color: #2c2c2c;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background-color: #ffffff;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            padding: 45px 55px;
            max-width: 650px;
            width: 100%;
            text-align: center;
        }
        .header { margin-bottom: 30px; }
        .header h1 {
            font-size: 26px;
            font-weight: 300;
            color: #2c2c2c;
        }
        .header .subtitle {
            font-size: 14px;
            color: #999999;
            margin-top: 8px;
            font-weight: 300;
        }
        .upload-area {
            border: 2px dashed #d0d0d0;
            border-radius: 12px;
            padding: 35px 20px;
            margin: 20px 0 25px 0;
            background-color: #f8f8f8;
        }
        .upload-area input[type="file"] { display: none; }
        .upload-label {
            display: inline-block;
            padding: 12px 35px;
            background-color: #e0e0e0;
            color: #3a3a3a;
            border-radius: 8px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 400;
            transition: background-color 0.3s ease;
        }
        .upload-label:hover { background-color: #d0d0d0; }
        .file-name {
            margin-top: 15px;
            font-size: 14px;
            color: #777777;
            min-height: 24px;
            font-weight: 300;
        }
        .btn-start {
            background-color: #4a4a4a;
            color: #ffffff;
            border: none;
            padding: 14px 50px;
            border-radius: 8px;
            font-size: 17px;
            font-weight: 400;
            cursor: pointer;
            transition: all 0.3s ease;
            width: 100%;
            margin-top: 5px;
        }
        .btn-start:hover { background-color: #333333; }
        .btn-start:disabled {
            background-color: #bbbbbb;
            cursor: not-allowed;
        }
        .cache-info {
            font-size: 12px;
            color: #aaaaaa;
            margin-top: 15px;
            font-weight: 300;
        }
        .cache-info span {
            color: #4a4a4a;
            font-weight: 400;
        }
        .error {
            background-color: #fff0f0;
            color: #cc4444;
            padding: 14px;
            border-radius: 8px;
            margin: 15px 0 10px 0;
            font-size: 14px;
            border-right: 3px solid #cc4444;
        }
        .success {
            background-color: #f0fff0;
            color: #44aa44;
            padding: 14px;
            border-radius: 8px;
            margin: 15px 0 10px 0;
            font-size: 14px;
            border-right: 3px solid #44aa44;
        }
        .footer {
            margin-top: 25px;
            font-size: 12px;
            color: #bbbbbb;
        }
        .required-columns {
            font-size: 12px;
            color: #aaaaaa;
            margin-top: 10px;
            background-color: #f8f8f8;
            padding: 12px;
            border-radius: 8px;
            text-align: center;
            line-height: 1.8;
        }
        .required-columns span {
            font-weight: 500;
            color: #555555;
            background-color: #eeeeee;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
        }
        .loading-text {
            display: none;
            margin: 15px 0 8px 0;
            font-size: 15px;
            color: #666666;
        }
        .loading-text.show { display: block; }
        .progress-wrap {
            display: none;
            margin: 12px 0 8px 0;
        }
        .progress-wrap.show { display: block; }
        .progress-bar-bg {
            height: 10px;
            background: #ececec;
            border-radius: 20px;
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            width: 35%;
            background: linear-gradient(90deg, #8a8a8a, #4a4a4a);
            border-radius: 20px;
            animation: loading-slide 1.3s ease-in-out infinite;
        }
        @keyframes loading-slide {
            0% { transform: translateX(-120%); }
            100% { transform: translateX(320%); }
        }
        .choice-grid {
            display: grid;
            gap: 12px;
            margin: 18px 0 10px 0;
        }
        .choice-btn {
            display: block;
            width: 100%;
            border: none;
            border-radius: 10px;
            padding: 16px 18px;
            font-size: 15px;
            cursor: pointer;
            text-align: center;
        }
        .choice-btn.primary { background: #4a4a4a; color: #fff; }
        .choice-btn.secondary { background: #efefef; color: #333; }
        .panel { display: none; }
        .panel.active { display: block; }
        .progress-bar-fill.determinate {
            animation: none;
            width: 0%;
            transition: width 0.25s ease;
        }
        .progress-percent {
            margin-top: 8px;
            font-size: 20px;
            color: #333;
        }
        @media (max-width: 480px) {
            .container { padding: 25px 18px; }
            .header h1 { font-size: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>تحلیل آمار تماس‌ها و سفرهای آسانسور</h1>
            <div class="subtitle">همان مرحله انتخاب فایل و پیشرفت، این‌بار داخل وب</div>
        </div>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <div id="choicePanel" class="panel active">
            <div class="choice-grid">
                <button type="button" class="choice-btn primary" id="chooseAnalyze">انتخاب فایل CSV و شروع تحلیل</button>
                <button type="button" class="choice-btn secondary" id="chooseSkip">رفتن مستقیم به صفحه وب (بدون انتخاب فایل)</button>
            </div>
        </div>
        <div id="uploadPanel" class="panel">
            <div class="required-columns">
                ستون‌های مورد نیاز: <span>id</span>  <span>elevator_id</span>  <span>event_time</span>  <span>floor_number</span>  <span>call_type</span>  <span>event_type</span>  <span>created_at</span>
            </div>
            <form id="uploadForm">
                <div class="upload-area">
                    <input type="file" id="fileInput" name="file" accept=".csv">
                    <label for="fileInput" class="upload-label">انتخاب فایل CSV</label>
                    <div class="file-name" id="fileName">هیچ فایلی انتخاب نشده است</div>
                </div>
                <button type="submit" class="btn-start" id="startBtn">شروع تحلیل</button>
                <div class="loading-text" id="loadingText">در حال پردازش داده‌ها، لطفاً صبر کنید...</div>
                <div class="progress-wrap" id="progressWrap">
                    <div class="progress-bar-bg"><div class="progress-bar-fill determinate" id="progressFill"></div></div>
                    <div class="progress-percent" id="progressPercent">0%</div>
                </div>
                <div class="cache-info">بعد از اتمام تحلیل می‌توانید گزارش متنی را دانلود کنید</div>
            </form>
        </div>
        <div class="footer">فرمت پشتیبانی شده: CSV</div>
    </div>
    <script>
        const choicePanel = document.getElementById('choicePanel');
        const uploadPanel = document.getElementById('uploadPanel');
        document.getElementById('chooseAnalyze').addEventListener('click', function() {
            choicePanel.classList.remove('active');
            uploadPanel.classList.add('active');
        });
        document.getElementById('chooseSkip').addEventListener('click', function() {
            choicePanel.classList.remove('active');
            uploadPanel.classList.add('active');
        });
        document.getElementById('fileInput').addEventListener('change', function(e) {
            document.getElementById('fileName').textContent = e.target.files[0] ? e.target.files[0].name : 'هیچ فایلی انتخاب نشده است';
        });
        document.getElementById('uploadForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const fileInput = document.getElementById('fileInput');
            if (!fileInput.files.length) {
                alert('لطفا یک فایل انتخاب کنید');
                return;
            }
            const startBtn = document.getElementById('startBtn');
            startBtn.disabled = true;
            startBtn.textContent = 'در حال پردازش...';
            document.getElementById('loadingText').classList.add('show');
            document.getElementById('progressWrap').classList.add('show');
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            const res = await fetch('/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (!res.ok) {
                alert(data.error || 'خطا در آپلود');
                startBtn.disabled = false;
                startBtn.textContent = 'شروع تحلیل';
                return;
            }
            const jobId = data.job_id;
            const fill = document.getElementById('progressFill');
            const percentEl = document.getElementById('progressPercent');
            const textEl = document.getElementById('loadingText');
            const timer = setInterval(async function() {
                const p = await fetch('/progress/' + jobId);
                const st = await p.json();
                fill.style.width = (st.percent || 0) + '%';
                percentEl.textContent = Math.round(st.percent || 0) + '%';
                textEl.textContent = st.message || 'در حال پردازش...';
                if (st.status === 'done') {
                    clearInterval(timer);
                    window.location.href = '/result';
                } else if (st.status === 'error') {
                    clearInterval(timer);
                    alert(st.error || 'خطا در تحلیل');
                    startBtn.disabled = false;
                    startBtn.textContent = 'شروع تحلیل';
                }
            }, 700);
        });
    </script>
</body>
</html>
'''

# ================ HTML صفحه نتایج ================
RESULT_HTML = '''
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>گزارش جامع تحلیل آسانسور</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #e8e8e8;
            color: #2c2c2c;
            padding: 25px 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1500px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.06);
            padding: 45px 55px;
        }
        .header {
            border-bottom: 2px solid #e8e8e8;
            padding-bottom: 22px;
            margin-bottom: 35px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
        }
        .header-left h1 {
            font-size: 28px;
            font-weight: 300;
            color: #1a1a1a;
        }
        .header-left .subtitle {
            font-size: 14px;
            color: #999999;
            margin-top: 6px;
        }
        .header-right {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }
        .badge {
            padding: 6px 18px;
            border-radius: 30px;
            font-size: 13px;
            font-weight: 400;
        }
        .badge-cache {
            background: #e8f5e8;
            color: #3a8a3a;
            border: 1px solid #c8e8c8;
        }
        .badge-file {
            background: #f0f0f0;
            color: #555555;
            border: 1px solid #e0e0e0;
        }
        .badge-time {
            background: #f5f0e8;
            color: #8a7a3a;
            border: 1px solid #e8dcc8;
        }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 14px;
            margin-bottom: 40px;
        }
        .summary-card {
            background: #f7f7f7;
            border-radius: 14px;
            padding: 18px 14px;
            text-align: center;
            border: 1px solid #eaeaea;
        }
        .summary-card .label {
            font-size: 11px;
            color: #999999;
            font-weight: 400;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .summary-card .value {
            font-size: 26px;
            font-weight: 300;
            color: #1a1a1a;
            margin-top: 6px;
        }
        .summary-card .value.small { font-size: 18px; }
        
        .section-title {
            font-size: 22px;
            font-weight: 300;
            color: #1a1a1a;
            margin: 40px 0 18px 0;
            padding-bottom: 12px;
            border-bottom: 2px solid #e8e8e8;
        }
        
        .daily-cards {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 15px;
            margin: 20px 0 30px 0;
        }
        
        .daily-card {
            background: #f8f8f8;
            border-radius: 12px;
            padding: 18px 20px;
            border: 1px solid #eaeaea;
            transition: all 0.2s ease;
        }
        
        .daily-card:hover {
            background: #f0f0f0;
            border-color: #d0d0d0;
        }
        
        .daily-card .date {
            font-size: 18px;
            font-weight: 500;
            color: #1a1a1a;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e0e0e0;
        }
        
        .daily-card .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            font-size: 13px;
            color: #555555;
        }
        
        .daily-card .stat-row .stat-label {
            color: #888888;
        }
        
        .daily-card .stat-row .stat-value {
            font-weight: 500;
            color: #2c2c2c;
        }
        
        .daily-card .stat-row .stat-value.highlight {
            color: #4a4a4a;
            font-weight: 600;
        }
        
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 25px;
            margin: 20px 0 10px 0;
        }
        .info-box {
            background: #f7f7f7;
            border-radius: 14px;
            padding: 22px 28px;
            border: 1px solid #eaeaea;
        }
        .info-box h3 {
            font-size: 16px;
            font-weight: 400;
            color: #444444;
            margin-bottom: 14px;
            border-bottom: 1px solid #eaeaea;
            padding-bottom: 10px;
        }
        .info-box ul {
            list-style: none;
            padding: 0;
        }
        .info-box ul li {
            padding: 7px 0;
            font-size: 14px;
            color: #555555;
            border-bottom: 1px solid #f0f0f0;
            display: flex;
            justify-content: space-between;
        }
        .info-box ul li:last-child { border-bottom: none; }
        .info-box ul li .num {
            font-weight: 500;
            color: #1a1a1a;
        }
        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            margin-top: 30px;
            padding-top: 25px;
            border-top: 2px solid #e8e8e8;
        }
        .btn-back {
            display: inline-block;
            background: #4a4a4a;
            color: #ffffff;
            border: none;
            padding: 12px 40px;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 400;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.3s ease;
        }
        .btn-back:hover {
            background: #333333;
        }
        .footer {
            margin-top: 35px;
            text-align: center;
            font-size: 12px;
            color: #bbbbbb;
            border-top: 1px solid #eaeaea;
            padding-top: 22px;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 10px;
        }
        .footer .left { text-align: left; }
        .footer .right { text-align: right; }
        
        @media (max-width: 992px) {
            .container { padding: 30px 25px; }
        }
        @media (max-width: 768px) {
            .container { padding: 20px 15px; }
            .header { flex-direction: column; align-items: flex-start; gap: 12px; }
            .summary-grid { grid-template-columns: repeat(2, 1fr); }
            .daily-cards { grid-template-columns: 1fr; }
            .info-grid { grid-template-columns: 1fr; }
            .footer { flex-direction: column; text-align: center; }
            .footer .left, .footer .right { text-align: center; }
        }
        @media (max-width: 480px) {
            .summary-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
            .summary-card { padding: 12px 8px; }
            .summary-card .value { font-size: 18px; }
            .container { padding: 15px 12px; }
            .header-left h1 { font-size: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-left">
                <h1>گزارش جامع تحلیل آسانسور</h1>
                <div class="subtitle">پردازش رویدادهای تماس و سفر</div>
            </div>
            <div class="header-right">
                <span class="badge badge-file">فایل: {{ filename }}</span>
                {% if from_cache %}
                <span class="badge badge-cache">از کش بارگذاری شد</span>
                {% endif %}
                <span class="badge badge-time">{{ now }}</span>
            </div>
        </div>
        
        <div class="summary-grid">
            <div class="summary-card">
                <div class="label">تعداد روزهای تحلیل</div>
                <div class="value">{{ result.total_days }}</div>
            </div>
            <div class="summary-card">
                <div class="label">کل تماس‌های ثبت‌شده</div>
                <div class="value">{{ "{:,.0f}".format(result.total_calls) }}</div>
            </div>
            <div class="summary-card">
                <div class="label">تماس‌های ورودی</div>
                <div class="value small">{{ "{:,.0f}".format(result.total_incalls) }}</div>
            </div>
            <div class="summary-card">
                <div class="label">اپکال (به سمت بالا)</div>
                <div class="value small">{{ "{:,.0f}".format(result.total_upcalls) }}</div>
            </div>
            <div class="summary-card">
                <div class="label">داونکال (به سمت پایین)</div>
                <div class="value small">{{ "{:,.0f}".format(result.total_downcalls) }}</div>
            </div>
            <div class="summary-card">
                <div class="label">میانگین زمان انتظار</div>
                <div class="value small">{{ "{:.1f}".format(result.avg_wait_time) }} ثانیه</div>
            </div>
            <div class="summary-card">
                <div class="label">میانگین زمان سفر</div>
                <div class="value small">{{ "{:.1f}".format(result.avg_travel_time) }} ثانیه</div>
            </div>
            <div class="summary-card">
                <div class="label">بازه زمانی داده‌ها</div>
                <div class="value small" style="font-size:11px; line-height:1.6;">
                    {{ result.min_date.strftime('%Y-%m-%d') if result.min_date else '' }}<br>
                    <span style="font-size:10px; color:#999;">تا</span><br>
                    {{ result.max_date.strftime('%Y-%m-%d') if result.max_date else '' }}
                </div>
            </div>
        </div>
        
        <div class="section-title">آمار روزانه تماس‌ها</div>
        
        <div class="daily-cards">
            {% for item in daily_data %}
            <div class="daily-card">
                <div class="date">{{ item.day }}</div>
                <div class="stat-row">
                    <span class="stat-label">کل تماس‌ها</span>
                    <span class="stat-value highlight">{{ "{:,.0f}".format(item.total_calls) }}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">تماس ورودی</span>
                    <span class="stat-value">{{ "{:,.0f}".format(item.incalls) }}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">اپکال</span>
                    <span class="stat-value">{{ "{:,.0f}".format(item.upcalls) }}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">داونکال</span>
                    <span class="stat-value">{{ "{:,.0f}".format(item.downcalls) }}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">میانگین انتظار</span>
                    <span class="stat-value">{{ "{:.1f}".format(item.avg_wait_time) }} ثانیه</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">میانگین سفر</span>
                    <span class="stat-value">{{ "{:.1f}".format(item.avg_travel_time) }} ثانیه</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">تعداد آسانسورها</span>
                    <span class="stat-value">{{ item.unique_elevators }}</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div class="section-title">تحلیل‌های تکمیلی</div>
        
        <div class="info-grid">
            <div class="info-box">
                <h3>توزیع تماس‌ها بر اساس طبقه</h3>
                <ul>
                {% for floor, count in result.floor_stats.head(10).items() %}
                    <li><span>طبقه {{ floor }}</span> <span class="num">{{ "{:,.0f}".format(count) }}</span></li>
                {% endfor %}
                </ul>
            </div>
            
            <div class="info-box">
                <h3>آسانسورهای پرمصرف</h3>
                <ul>
                {% for elev, count in result.elevator_stats.head(10).items() %}
                    <li><span>آسانسور {{ elev }}</span> <span class="num">{{ "{:,.0f}".format(count) }}</span></li>
                {% endfor %}
                </ul>
            </div>
            
            <div class="info-box">
                <h3>توزیع نوع تماس</h3>
                <ul>
                {% for call_type, count in result.call_type_stats.items() %}
                    <li>
                        <span>
                            {% if call_type == 'INCALL' %}تماس ورودی
                            {% elif call_type == 'UPCALL' %}اپکال
                            {% elif call_type == 'DOWNCALL' %}داونکال
                            {% else %}{{ call_type }}{% endif %}
                        </span>
                        <span class="num">{{ "{:,.0f}".format(count) }} ({{ "{:.1f}".format(count / result.total_calls * 100) }}%)</span>
                    </li>
                {% endfor %}
                </ul>
            </div>
            
            <div class="info-box">
                <h3>اطلاعات کلی</h3>
                <ul>
                    <li><span>کل تماس‌های معتبر</span> <span class="num">{{ "{:,.0f}".format(result.total_calls) }}</span></li>
                    <li><span>تعداد روزهای تحلیل</span> <span class="num">{{ result.total_days }}</span></li>
                    <li><span>بیشترین تماس در یک روز</span> <span class="num">{{ "{:,.0f}".format(result.daily_df['total_calls'].max()) }}</span></li>
                    <li><span>کمترین تماس در یک روز</span> <span class="num">{{ "{:,.0f}".format(result.daily_df['total_calls'].min()) }}</span></li>
                    <li><span>میانگین تماس در روز</span> <span class="num">{{ "{:.1f}".format(result.total_calls / result.total_days) }}</span></li>
                </ul>
            </div>
        </div>
        
        <div class="actions">
            <a href="/" class="btn-back">بازگشت به صفحه اصلی</a>
            <a href="/download-report" class="btn-back">دانلود گزارش متنی</a>
        </div>
        
        <div class="footer">
            <div class="left">نتایج تا ۳ روز در کش ذخیره می‌شود</div>
            <div class="right">تحلیل‌گر هوشمند آسانسور</div>
        </div>
    </div>
</body>
</html>
'''

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def report_progress(callback, percent, message):
    if callback:
        callback(max(0, min(100, percent)), message)


def analyze_elevator_data(file_path, progress_callback=None):
    df = None
    encodings = ['utf-8', 'latin1', 'cp1252', 'utf-16', 'ansi', 'cp1256']

    report_progress(progress_callback, 3, "در حال خواندن فایل CSV...")
    for enc in encodings:
        try:
            df = pd.read_csv(file_path, encoding=enc)
            break
        except:
            continue
    
    if df is None:
        df = pd.read_csv(file_path, encoding='utf-8', errors='ignore')
    
    required_cols = ['id', 'elevator_id', 'event_time', 'floor_number', 'call_type', 'event_type', 'created_at']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return None, f"ستون‌های زیر در فایل وجود ندارند: {', '.join(missing_cols)}"

    report_progress(progress_callback, 10, "در حال پردازش زمان‌ها و مرتب‌سازی...")
    df['event_time'] = pd.to_datetime(df['event_time'], errors='coerce')
    df['created_at'] = pd.to_datetime(df['created_at'], errors='coerce')
    df = df.dropna(subset=['event_time'])
    df['date'] = df['event_time'].dt.date
    
    df_sorted = df.sort_values(['event_time'])
    added_df = df_sorted[df_sorted['event_type'] == 'ADDED']
    removed_df = df_sorted[df_sorted['event_type'] == 'REMOVED']
    
    added_df['temp_key'] = added_df['call_type'].astype(str) + '_' + added_df['elevator_id'].astype(str) + '_' + added_df['floor_number'].astype(str)
    removed_df['temp_key'] = removed_df['call_type'].astype(str) + '_' + removed_df['elevator_id'].astype(str) + '_' + removed_df['floor_number'].astype(str)
    
    results = []
    keys = added_df['temp_key'].unique()
    total_keys = len(keys) or 1
    update_every = max(1, total_keys // 100)

    report_progress(progress_callback, 15, "در حال تطبیق تماس‌ها...")
    for i, key in enumerate(keys):
        added_group = added_df[added_df['temp_key'] == key].sort_values('event_time')
        removed_group = removed_df[removed_df['temp_key'] == key].sort_values('event_time')
        
        for _, added_row in added_group.iterrows():
            future_removed = removed_group[removed_group['event_time'] > added_row['event_time']]
            
            if not future_removed.empty:
                closest_removed = future_removed.iloc[0]
                time_diff = (closest_removed['event_time'] - added_row['event_time']).total_seconds()
                
                if 2 <= time_diff <= 600:
                    call_type = added_row['call_type']
                    
                    if call_type == 'INCALL':
                        results.append({
                            'call_id': added_row['id'],
                            'date': added_row['date'],
                            'elevator_id': added_row['elevator_id'],
                            'floor_number': added_row['floor_number'],
                            'call_type': call_type,
                            'wait_time': time_diff,
                            'travel_time': 0,
                            'start_time': added_row['event_time'],
                            'end_time': closest_removed['event_time']
                        })
                    elif call_type in ['DOWNCALL', 'UPCALL']:
                        results.append({
                            'call_id': added_row['id'],
                            'date': added_row['date'],
                            'elevator_id': added_row['elevator_id'],
                            'floor_number': added_row['floor_number'],
                            'call_type': call_type,
                            'wait_time': 0,
                            'travel_time': time_diff,
                            'start_time': added_row['event_time'],
                            'end_time': closest_removed['event_time']
                        })
                    
                    removed_group = removed_group[removed_group['id'] != closest_removed['id']]

        if i % update_every == 0 or i == total_keys - 1:
            pct = 15 + int((i + 1) / total_keys * 70)
            report_progress(
                progress_callback,
                pct,
                f"در حال تطبیق تماس‌ها... {i + 1:,} از {total_keys:,}"
            )
    
    results_df = pd.DataFrame(results)
    
    if len(results_df) == 0:
        return None, "هیچ تماس کاملی پیدا نشد!"

    report_progress(progress_callback, 88, "در حال حذف داده‌های پرت...")
    
    def remove_outliers_iqr(data, column):
        Q1 = data[column].quantile(0.25)
        Q3 = data[column].quantile(0.75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR
        return data[(data[column] >= lower_bound) & (data[column] <= upper_bound)]
    
    if 'wait_time' in results_df.columns:
        wait_data = results_df[results_df['wait_time'] > 0]
        if not wait_data.empty:
            results_df = remove_outliers_iqr(results_df, 'wait_time')
    
    if 'travel_time' in results_df.columns:
        travel_data = results_df[results_df['travel_time'] > 0]
        if not travel_data.empty:
            results_df = remove_outliers_iqr(results_df, 'travel_time')
    
    daily_stats = []
    unique_days = sorted(results_df['date'].unique())
    report_progress(progress_callback, 92, "در حال محاسبه آمار روزانه...")
    
    for day in unique_days:
        day_data = results_df[results_df['date'] == day]
        
        total_calls = len(day_data)
        incalls = len(day_data[day_data['call_type'] == 'INCALL'])
        upcalls = len(day_data[day_data['call_type'] == 'UPCALL'])
        downcalls = len(day_data[day_data['call_type'] == 'DOWNCALL'])
        
        wait_times = day_data[day_data['call_type'] == 'INCALL']['wait_time']
        total_wait_time = wait_times.sum() if not wait_times.empty else 0
        avg_wait_time = wait_times.mean() if not wait_times.empty else 0
        max_wait_time = wait_times.max() if not wait_times.empty else 0
        min_wait_time = wait_times.min() if not wait_times.empty else 0
        
        travel_times = day_data[day_data['call_type'].isin(['DOWNCALL', 'UPCALL'])]['travel_time']
        total_travel_time = travel_times.sum() if not travel_times.empty else 0
        avg_travel_time = travel_times.mean() if not travel_times.empty else 0
        max_travel_time = travel_times.max() if not travel_times.empty else 0
        min_travel_time = travel_times.min() if not travel_times.empty else 0
        
        unique_elevators = day_data['elevator_id'].nunique()
        
        daily_stats.append({
            'day': day,
            'total_calls': total_calls,
            'incalls': incalls,
            'upcalls': upcalls,
            'downcalls': downcalls,
            'avg_wait_time': avg_wait_time,
            'total_wait_time': total_wait_time,
            'max_wait_time': max_wait_time,
            'min_wait_time': min_wait_time,
            'avg_travel_time': avg_travel_time,
            'total_travel_time': total_travel_time,
            'max_travel_time': max_travel_time,
            'min_travel_time': min_travel_time,
            'unique_elevators': unique_elevators
        })
    
    daily_df = pd.DataFrame(daily_stats)
    
    floor_stats = results_df.groupby('floor_number').size().sort_values(ascending=False)
    elevator_stats = results_df.groupby('elevator_id').size().sort_values(ascending=False)
    call_type_stats = results_df['call_type'].value_counts()
    report_progress(progress_callback, 100, "تحلیل کامل شد")
    return {
        'daily_df': daily_df,
        'results_df': results_df,
        'floor_stats': floor_stats,
        'elevator_stats': elevator_stats,
        'call_type_stats': call_type_stats,
        'total_days': len(daily_df),
        'total_calls': daily_df['total_calls'].sum(),
        'total_incalls': daily_df['incalls'].sum(),
        'total_upcalls': daily_df['upcalls'].sum(),
        'total_downcalls': daily_df['downcalls'].sum(),
        'avg_wait_time': daily_df['avg_wait_time'].mean(),
        'avg_travel_time': daily_df['avg_travel_time'].mean(),
        'min_date': daily_df['day'].min(),
        'max_date': daily_df['day'].max()
    }, None

def get_current_result():
    job_id = session.get('job_id')
    if job_id and job_id in JOBS and JOBS[job_id].get('status') == 'done':
        return JOBS[job_id]
    return session.get('result')


def run_analysis_job(job_id):
    job = JOBS[job_id]

    def cb(percent, message):
        job['percent'] = percent
        job['message'] = message

    try:
        result, error = analyze_elevator_data(job['tmp_path'], cb)
        if error or result is None:
            job['status'] = 'error'
            job['error'] = error or 'هیچ تماس کاملی پیدا نشد.'
            return
        json_result = make_json_safe(result)
        job['result'] = json_result
        job['report'] = build_text_report(result['daily_df'])
        job['percent'] = 100
        job['message'] = 'تحلیل کامل شد'
        job['status'] = 'done'
        set_cache_result(job['file_hash'], json_result)
    except Exception as e:
        job['status'] = 'error'
        job['error'] = str(e)
    finally:
        try:
            os.unlink(job['tmp_path'])
        except Exception:
            pass


@app.route('/', methods=['GET'])
def index():
    return render_template_string(INDEX_HTML)


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لطفا یک فایل انتخاب کنید'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لطفا یک فایل انتخاب کنید'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'فرمت فایل باید CSV باشد'}), 400

    file_content = file.read()
    file_hash = get_file_hash(file_content)
    job_id = uuid.uuid4().hex
    session['job_id'] = job_id

    cached_result = get_cached_result(file_hash)
    if cached_result:
        JOBS[job_id] = {
            'status': 'done',
            'percent': 100,
            'message': 'نتیجه از کش بارگذاری شد',
            'filename': file.filename,
            'result': cached_result,
            'report': None,
            'from_cache': True,
            'error': None
        }
        session['result'] = {
            'filename': file.filename,
            'result': cached_result,
            'from_cache': True
        }
        return jsonify({'job_id': job_id})

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
    tmp.write(file_content)
    tmp.close()
    JOBS[job_id] = {
        'status': 'running',
        'percent': 1,
        'message': 'شروع تحلیل...',
        'filename': file.filename,
        'file_hash': file_hash,
        'tmp_path': tmp.name,
        'result': None,
        'report': None,
        'from_cache': False,
        'error': None
    }
    threading.Thread(target=run_analysis_job, args=(job_id,), daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/progress/<job_id>')
def progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'status': 'error', 'error': 'وظیفه پیدا نشد', 'percent': 0}), 404
    return jsonify({
        'status': job['status'],
        'percent': job.get('percent', 0),
        'message': job.get('message', ''),
        'error': job.get('error')
    })


@app.route('/result')
def show_result():
    data = get_current_result()
    if not data or not data.get('result'):
        return render_template_string(INDEX_HTML, error='نتیجه‌ای برای نمایش وجود ندارد')

    filename = data['filename']
    json_result = data['result']
    from_cache = data.get('from_cache', False)
    result = restore_from_json(json_result)
    daily_data = []
    for _, row in result['daily_df'].iterrows():
        daily_data.append({
            'day': row['day'].strftime('%Y-%m-%d') if hasattr(row['day'], 'strftime') else str(row['day']),
            'total_calls': int(row['total_calls']),
            'incalls': int(row['incalls']),
            'upcalls': int(row['upcalls']),
            'downcalls': int(row['downcalls']),
            'avg_wait_time': float(row['avg_wait_time']),
            'avg_travel_time': float(row['avg_travel_time']),
            'unique_elevators': int(row['unique_elevators'])
        })
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    return render_template_string(
        RESULT_HTML,
        result=result,
        daily_data=daily_data,
        filename=filename,
        from_cache=from_cache,
        now=now
    )


@app.route('/download-report')
def download_report():
    data = get_current_result()
    if not data or not data.get('result'):
        return 'نتیجه‌ای برای دانلود وجود ندارد', 404
    report = data.get('report')
    if not report:
        result = restore_from_json(data['result'])
        report = build_text_report(result['daily_df'])
    buf = io.BytesIO(report.encode('utf-8'))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name='elevator_call_analysis.txt',
        mimetype='text/plain'
    )

def build_text_report(daily_df):
    lines = []
    lines.append("=" * 70)
    lines.append("گزارش کامل آمار روزانه تماس‌ها و سفرهای آسانسور".center(70))
    lines.append("=" * 70)
    lines.append(f"\nبازه زمانی: از {daily_df['day'].min()} تا {daily_df['day'].max()}")
    lines.append(f"تعداد روزهای تحلیل: {len(daily_df)}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("خلاصه کلی دوره".center(70))
    lines.append("=" * 70)
    lines.append(f"مجموع تماس‌ها: {daily_df['total_calls'].sum():,}")
    lines.append(f"تماس‌های ورودی (INCALL): {daily_df['incalls'].sum():,}")
    lines.append(f"مجموع اپکال‌ها: {daily_df['upcalls'].sum():,}")
    lines.append(f"مجموع داونکال‌ها: {daily_df['downcalls'].sum():,}")
    lines.append(f"میانگین کلی زمان انتظار: {daily_df['avg_wait_time'].mean():.1f} ثانیه")
    lines.append(f"میانگین کلی زمان سفر: {daily_df['avg_travel_time'].mean():.1f} ثانیه")
    lines.append("")
    lines.append("=" * 70)
    lines.append("آمار تفصیلی روزانه".center(70))
    lines.append("=" * 70)

    for idx, row in daily_df.iterrows():
        day_str = row['day'].strftime("%Y-%m-%d")
        lines.append("")
        lines.append("-" * 55)
        lines.append(f"روز {idx + 1}: {day_str}")
        lines.append("-" * 55)
        lines.append(f"   کل تماس‌ها: {row['total_calls']:,}")
        lines.append(f"   تماس‌های ورودی (INCALL): {row['incalls']:,}")
        lines.append(f"   اپکال (UPCALL): {row['upcalls']:,}")
        lines.append(f"   داونکال (DOWNCALL): {row['downcalls']:,}")
        lines.append(f"   تعداد آسانسورهای استفاده شده: {row['unique_elevators']}")
        if row['incalls'] > 0:
            lines.append("   زمان انتظار:")
            lines.append(f"      - میانگین: {row['avg_wait_time']:.1f} ثانیه")
            lines.append(f"      - مجموع: {row['total_wait_time']:.0f} ثانیه")
            lines.append(f"      - بیشترین: {row['max_wait_time']:.1f} ثانیه")
            lines.append(f"      - کمترین: {row['min_wait_time']:.1f} ثانیه")
        else:
            lines.append("   زمان انتظار: داده‌ای وجود ندارد")
        if row['upcalls'] > 0 or row['downcalls'] > 0:
            lines.append("   زمان سفر:")
            lines.append(f"      - میانگین: {row['avg_travel_time']:.1f} ثانیه")
            lines.append(f"      - مجموع: {row['total_travel_time']:.0f} ثانیه")
            lines.append(f"      - بیشترین: {row['max_travel_time']:.1f} ثانیه")
            lines.append(f"      - کمترین: {row['min_travel_time']:.1f} ثانیه")
        else:
            lines.append("   زمان سفر: داده‌ای وجود ندارد")

    lines.append("")
    lines.append("=" * 70)
    lines.append("گزارش در تاریخ {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")).center(70))
    lines.append("=" * 70)
    return '\n'.join(lines)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
