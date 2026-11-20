import React from 'react';

export default function DataTable({ columns }) {
  if (!columns) return null;

  const colNames = Object.keys(columns);
  const metrics = ['mean', 'median', 'stddev', 'min', 'max', 'p25', 'p75'];

  return (
    <div style={{ marginBottom: 24, overflowX: 'auto' }}>
      <h2>Summary Table</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12, background: '#fff', borderRadius: 8 }}>
        <thead>
          <tr style={{ background: '#f0f0f0' }}>
            <th style={{ padding: '8px 12px', textAlign: 'left', borderBottom: '2px solid #ddd' }}>Metric</th>
            {colNames.map(name => (
              <th key={name} style={{ padding: '8px 12px', textAlign: 'right', borderBottom: '2px solid #ddd' }}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.map(metric => (
            <tr key={metric}>
              <td style={{ padding: '6px 12px', fontWeight: 500, borderBottom: '1px solid #eee' }}>{metric}</td>
              {colNames.map(name => (
                <td key={name} style={{ padding: '6px 12px', textAlign: 'right', borderBottom: '1px solid #eee' }}>
                  {columns[name][metric] != null ? Number(columns[name][metric]).toFixed(4) : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
