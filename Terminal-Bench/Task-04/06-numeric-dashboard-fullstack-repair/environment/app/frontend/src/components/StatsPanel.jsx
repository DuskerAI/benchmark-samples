import React from 'react';

export default function StatsPanel({ columns, rowCount }) {
  if (!columns) return null;

  return (
    <div style={{ marginBottom: 24 }}>
      <h2>Descriptive Statistics ({rowCount} rows)</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16, marginTop: 12 }}>
        {Object.entries(columns).map(([name, stats]) => (
          <div key={name} style={{ background: '#fff', borderRadius: 8, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
            <h3 style={{ marginBottom: 8, color: '#1a73e8' }}>{name}</h3>
            <table style={{ width: '100%', fontSize: 14 }}>
              <tbody>
                {Object.entries(stats).map(([key, val]) => (
                  <tr key={key}>
                    <td style={{ padding: '2px 8px', fontWeight: 500 }}>{key}</td>
                    <td style={{ padding: '2px 8px', textAlign: 'right' }}>{typeof val === 'number' ? val.toFixed(4) : String(val)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
