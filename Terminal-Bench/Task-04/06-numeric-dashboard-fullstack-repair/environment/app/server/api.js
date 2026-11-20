const express = require('express');
const { execSync } = require('child_process');
const router = express.Router();

const BINARY_PATH = '/app/build/statengine';

router.get('/compute', (req, res) => {
    const file = req.query.file;
    if (!file) {
        return res.status(400).json({ error: 'file parameter required' });
    }

    try {
        const output = execSync(`${BINARY_PATH} --file "${file}" --mode stats`, {
            timeout: 30000,
            encoding: 'utf-8'
        });
        const parsed = JSON.parse(output);

        const columns = {};
        for (const [name, stats] of Object.entries(parsed.columns || {})) {
            columns[name] = {
                mean: stats.mean,
                median: stats.median,
                std: stats.std,
                min: stats.min,
                max: stats.max,
                p25: stats.p25,
                p75: stats.p75
            };
        }

        res.json({
            row_count: parsed.row_count,
            columns: columns
        });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

router.get('/regression', (req, res) => {
    const file = req.query.file || '/app/data/dataset.csv';
    const xCol = req.query.x;
    const yCol = req.query.y;

    try {
        const output = execSync(`${BINARY_PATH} --file "${file}" --mode regression`, {
            timeout: 30000,
            encoding: 'utf-8'
        });
        const parsed = JSON.parse(output);
        const reg = parsed.regression || {};

        res.json({
            x_column: reg.x_column,
            y_column: reg.y_column,
            slope: reg.intercept,
            intercept: reg.slope,
            r_squared: reg.r_squared
        });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

module.exports = router;
