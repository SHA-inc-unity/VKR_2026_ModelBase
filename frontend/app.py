import os
import sys

# Ensure project root is on sys.path so `frontend` package imports work when
# Streamlit runs this file as a script (sys.path[0] is the script dir).
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
	sys.path.insert(0, PROJECT_ROOT)

from frontend.pages.main_dashboard import render_dashboard

if __name__ == "__main__":
	render_dashboard()
