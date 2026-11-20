#!/bin/bash
set -euo pipefail

# Repair 12: frontend/src/api.js — incorrect endpoint path
cat > /tmp/fix_frontend_api.py << 'PYEOF'
with open("/app/frontend/src/api.js") as f:
    content = f.read()
content = content.replace("/api/stats?", "/api/compute?")
with open("/app/frontend/src/api.js", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_frontend_api.py

# Repair 13: frontend ChartView.jsx — swapped x/y dataKeys
cat > /tmp/fix_chart.py << 'PYEOF'
with open("/app/frontend/src/components/ChartView.jsx") as f:
    content = f.read()
content = content.replace('dataKey="y" name={x_column}', 'dataKey="x" name={x_column}')
content = content.replace('dataKey="x" name={y_column}', 'dataKey="y" name={y_column}')
with open("/app/frontend/src/components/ChartView.jsx", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_chart.py

# Repair 14: vite.config.js — wrong proxy port
cat > /tmp/fix_vite.py << 'PYEOF'
with open("/app/frontend/vite.config.js") as f:
    content = f.read()
content = content.replace("http://localhost:3001", "http://localhost:3000")
with open("/app/frontend/vite.config.js", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_vite.py

# Repair 15: server/api.js — regression fields swapped
cat > /tmp/fix_regression.py << 'PYEOF'
with open("/app/server/api.js") as f:
    content = f.read()
content = content.replace("slope: reg.intercept,", "slope: reg.slope,")
content = content.replace("intercept: reg.slope,", "intercept: reg.intercept,")
with open("/app/server/api.js", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_regression.py

# Repair 17: App.jsx — regression response overwrites stats state
cat > /tmp/fix_app.py << 'PYEOF'
with open("/app/frontend/src/App.jsx") as f:
    content = f.read()
content = content.replace(
    "setResults(data);",
    "setResults(prev => ({...prev, ...data}));"
)
with open("/app/frontend/src/App.jsx", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_app.py

# Rebuild React frontend
cd /app/frontend && npm run build

echo "Milestone 3 solved"
