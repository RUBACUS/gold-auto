from dotenv import load_dotenv
import os
load_dotenv()
print("FLASK_APP_URL:", os.environ.get("FLASK_APP_URL"))
print("FLASK_EDITOR_USERNAME:", os.environ.get("FLASK_EDITOR_USERNAME"))
print("FLASK_EDITOR_PASSWORD:", os.environ.get("FLASK_EDITOR_PASSWORD"))