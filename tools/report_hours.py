"""Report actual demo activity counts. No estimated business results."""
import json
from dossier_db import dashboard
if __name__ == "__main__":
    print(json.dumps({"demo": True, "activity_counts": dashboard()["kpi"]}, indent=2))
