use crate::decimal::{self, Interval};
use crate::engine::{BucketReport, Report};

pub fn render(report: &Report) -> String {
    let mut out = String::new();
    out.push_str("{\n");
    out.push_str(&format!("  \"portfolio\": \"{}\",\n", escape(&report.portfolio)));
    out.push_str(&format!("  \"scale\": {},\n", report.scale));
    out.push_str("  \"buckets\": [\n");
    for (idx, bucket) in report.buckets.iter().enumerate() {
        out.push_str("    ");
        out.push_str(&render_bucket(bucket, report.scale));
        if idx + 1 != report.buckets.len() {
            out.push(',');
        }
        out.push('\n');
    }
    out.push_str("  ],\n");
    out.push_str("  \"total\": {");
    out.push_str(&format!(
        "\"exposure\":{},\"variance\":{}",
        render_interval(report.total_exposure, report.scale),
        render_interval(report.total_variance, report.scale)
    ));
    out.push_str("}\n}");
    out
}

fn render_bucket(bucket: &BucketReport, scale: u32) -> String {
    format!(
        "{{\"bucket\":\"{}\",\"exposure\":{},\"variance\":{}}}",
        escape(&bucket.bucket),
        render_interval(bucket.exposure, scale),
        render_interval(bucket.variance, scale)
    )
}

fn render_interval(interval: Interval, scale: u32) -> String {
    format!(
        "{{\"lo\":\"{}\",\"hi\":\"{}\"}}",
        decimal::format_ticks(interval.lo, scale),
        decimal::format_ticks(interval.hi, scale)
    )
}

fn escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}
