# Elevator call analytics

Flask web app for uploading elevator CSV events, calculating wait/travel times, and showing a daily report.

## Local run

pip install -r requirements.txt
python web_data.py

Then open http://127.0.0.1:5000

## Deploy on Render

Connect this repo to https://render.com and it will use render.yaml.
Public URL will look like https://elevator-analytics.onrender.com
Add a custom domain later from the Render dashboard.

## Required CSV columns

id, elevator_id, event_time, floor_number, call_type, event_type, created_at
