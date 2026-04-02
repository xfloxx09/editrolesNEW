web: sh -c 'flask --app run.py db upgrade && gunicorn --bind 0.0.0.0:$PORT --timeout 600 "app:create_app()"'
