const BASE_URL = '';

export async function fetchStats(filePath) {
  const resp = await fetch(`${BASE_URL}/api/stats?file=${encodeURIComponent(filePath)}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

export async function fetchRegression(filePath, x, y) {
  const resp = await fetch(`${BASE_URL}/api/regression?file=${encodeURIComponent(filePath)}&x=${x}&y=${y}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  return resp.json();
}
