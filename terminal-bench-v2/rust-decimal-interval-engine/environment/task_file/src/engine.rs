use crate::decimal::{self, Interval};
use crate::parser::Portfolio;
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub struct BucketReport {
    pub bucket: String,
    pub exposure: Interval,
    pub variance: Interval,
}

#[derive(Clone, Debug)]
pub struct Report {
    pub portfolio: String,
    pub scale: u32,
    pub buckets: Vec<BucketReport>,
    pub total_exposure: Interval,
    pub total_variance: Interval,
}

pub fn evaluate(portfolio: &Portfolio) -> Result<Report, String> {
    let mut factor_exposure: BTreeMap<String, Interval> = BTreeMap::new();
    for name in portfolio.factors.keys() {
        factor_exposure.insert(name.clone(), decimal::zero());
    }

    for pos in &portfolio.positions {
        let term = decimal::mul(pos.amount, pos.sensitivity, portfolio.scale)?;
        let current = factor_exposure.get(&pos.factor).copied().unwrap_or_else(decimal::zero);
        factor_exposure.insert(pos.factor.clone(), decimal::add(current, term)?);
    }

    let mut bucket_names: Vec<String> = Vec::new();
    for factor_name in &portfolio.factor_order {
        let factor = &portfolio.factors[factor_name];
        if !bucket_names.iter().any(|b| b == &factor.bucket) {
            bucket_names.push(factor.bucket.clone());
        }
    }

    let mut buckets = Vec::new();
    let mut total_exposure = decimal::zero();
    let mut total_variance = decimal::zero();

    for bucket in bucket_names {
        let factor_names: Vec<&String> = portfolio
            .factor_order
            .iter()
            .filter(|name| portfolio.factors[*name].bucket == bucket)
            .collect();
        let mut exposure = decimal::zero();
        for name in &factor_names {
            exposure = decimal::add(exposure, factor_exposure[*name])?;
        }

        let mut variance = decimal::zero();
        for i in 0..factor_names.len() {
            for j in i..factor_names.len() {
                let a = factor_names[i];
                let b = factor_names[j];
                let coeff = if i == j {
                    portfolio.factors[a].variance
                } else {
                    portfolio
                        .covariances
                        .get(&(a.clone(), b.clone()))
                        .copied()
                        .unwrap_or_else(decimal::zero)
                };
                let mut term = decimal::mul(factor_exposure[a], factor_exposure[b], portfolio.scale)?;
                term = decimal::mul(term, coeff, portfolio.scale)?;
                if i != j {
                    term = decimal::double(term)?;
                }
                variance = decimal::add(variance, term)?;
            }
        }

        total_exposure = decimal::add(total_exposure, exposure)?;
        total_variance = decimal::add(total_variance, variance)?;
        buckets.push(BucketReport { bucket, exposure, variance });
    }

    Ok(Report {
        portfolio: portfolio.name.clone(),
        scale: portfolio.scale,
        buckets,
        total_exposure,
        total_variance,
    })
}
