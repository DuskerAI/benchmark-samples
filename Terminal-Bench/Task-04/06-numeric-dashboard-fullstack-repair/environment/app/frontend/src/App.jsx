import React, { useState } from 'react';
import { fetchStats, fetchRegression } from './api';
import StatsPanel from './components/StatsPanel';
import ChartView from './components/ChartView';
import DataTable from './components/DataTable';

export default function App() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleLoadStats = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchStats('/app/data/dataset.csv');
      setResults(data);
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  const handleLoadRegression = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRegression('/app/data/dataset.csv', 'height_cm', 'weight_kg');
      setResults(data);
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24 }}>
      <h1 style={{ marginBottom: 16 }}>Statistical Dashboard</h1>
      <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
        <button onClick={handleLoadStats} disabled={loading}
          style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Load Statistics
        </button>
        <button onClick={handleLoadRegression} disabled={loading}
          style={{ padding: '8px 16px', cursor: 'pointer' }}>
          Load Regression
        </button>
      </div>
      {error && <div style={{ color: 'red', marginBottom: 16 }}>{error}</div>}
      {loading && <div>Loading...</div>}
      {results && results.columns && <StatsPanel columns={results.columns} rowCount={results.row_count} />}
      {results && results.regression && <ChartView regression={results.regression} />}
      {results && results.columns && <DataTable columns={results.columns} />}
    </div>
  );
}
