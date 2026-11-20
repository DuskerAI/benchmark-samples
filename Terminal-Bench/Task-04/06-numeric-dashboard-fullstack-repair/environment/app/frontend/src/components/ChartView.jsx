import React from 'react';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

export default function ChartView({ regression }) {
  if (!regression) return null;

  const { x_column, y_column, slope, intercept, r_squared } = regression;

  const points = [];
  for (let x = 0; x <= 200; x += 10) {
    points.push({ x, y: slope * x + intercept });
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <h2>Regression: {y_column} vs {x_column}</h2>
      <p>Slope: {slope?.toFixed(4)} | Intercept: {intercept?.toFixed(4)} | R²: {r_squared?.toFixed(4)}</p>
      <ResponsiveContainer width="100%" height={400}>
        <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
          <CartesianGrid />
          <XAxis type="number" dataKey="y" name={x_column} />
          <YAxis type="number" dataKey="x" name={y_column} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} />
          <Scatter name="Regression Line" data={points} fill="#1a73e8" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
