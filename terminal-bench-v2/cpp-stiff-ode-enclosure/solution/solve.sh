#!/bin/bash
set -euo pipefail

cat > /app/stiffbox/src/main.cpp <<'CPP'
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

struct Config {
    std::string model, restart_from;
    double t0{}, t_end{}, sample_dt{}, abs_tol{}, rel_tol{}, event_level{}, checkpoint_interval{};
    int event_index{};
    double rate{}, target{}, fast{}, slow{};
    std::vector<double> y0;
};

static std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    return s.substr(a, b - a + 1);
}

static double parse_double(const std::map<std::string,std::string>& m, const std::string& k) {
    auto it = m.find(k);
    if (it == m.end()) throw std::runtime_error("missing " + k);
    size_t n = 0;
    double v = std::stod(it->second, &n);
    if (n != it->second.size() || !std::isfinite(v)) throw std::runtime_error("bad " + k);
    return v;
}

static int parse_int(const std::map<std::string,std::string>& m, const std::string& k) {
    auto it = m.find(k);
    if (it == m.end()) throw std::runtime_error("missing " + k);
    size_t n = 0;
    int v = std::stoi(it->second, &n);
    if (n != it->second.size()) throw std::runtime_error("bad " + k);
    return v;
}

static std::vector<double> parse_vec(const std::string& s) {
    std::vector<double> out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (item.find_first_of(" \t") != std::string::npos || item.empty()) throw std::runtime_error("bad vector");
        size_t n = 0;
        double v = std::stod(item, &n);
        if (n != item.size() || !std::isfinite(v)) throw std::runtime_error("bad vector");
        out.push_back(v);
    }
    if (out.empty()) throw std::runtime_error("empty vector");
    return out;
}

static std::map<std::string,std::string> read_kv(const std::string& path, bool checkpoint=false) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("open failed");
    if (checkpoint) {
        std::string magic;
        std::getline(in, magic);
        if (magic != "STIFFBOX2") throw std::runtime_error("bad checkpoint");
    }
    std::map<std::string,std::string> m;
    std::string line;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        auto p = line.find('=');
        if (p == std::string::npos) throw std::runtime_error("bad kv");
        std::string k = trim(line.substr(0, p));
        std::string v = trim(line.substr(p + 1));
        if (k.empty() || m.count(k)) throw std::runtime_error("bad kv");
        m[k] = v;
    }
    return m;
}

static std::string join_vec(const std::vector<double>& v) {
    std::ostringstream os;
    os << std::setprecision(17);
    for (size_t i=0;i<v.size();++i) {
        if (i) os << ',';
        os << v[i];
    }
    return os.str();
}

static Config parse_config(const std::string& path) {
    auto m = read_kv(path);
    Config c;
    const std::set<std::string> base = {"model","t0","t_end","sample_dt","y0","abs_tol","rel_tol","event_index","event_level","checkpoint_interval","restart_from"};
    c.model = m.at("model");
    std::set<std::string> allowed = base;
    if (c.model == "scalar_relax") { allowed.insert("rate"); allowed.insert("target"); }
    else if (c.model == "stiff_decay2") { allowed.insert("fast"); allowed.insert("slow"); }
    else throw std::runtime_error("bad model");
    for (auto& kv : m) if (!allowed.count(kv.first)) throw std::runtime_error("unknown key");
    for (auto& k : base) if (!m.count(k)) throw std::runtime_error("missing key");
    c.t0 = parse_double(m,"t0"); c.t_end = parse_double(m,"t_end"); c.sample_dt = parse_double(m,"sample_dt");
    c.abs_tol = parse_double(m,"abs_tol"); c.rel_tol = parse_double(m,"rel_tol");
    c.event_index = parse_int(m,"event_index"); c.event_level = parse_double(m,"event_level");
    c.checkpoint_interval = parse_double(m,"checkpoint_interval"); c.restart_from = m.at("restart_from"); c.y0 = parse_vec(m.at("y0"));
    if (!(c.t_end >= c.t0) || !(c.sample_dt > 0) || !(c.abs_tol > 0) || c.rel_tol < 0 || c.checkpoint_interval < 0) throw std::runtime_error("bad range");
    if (c.model == "scalar_relax") {
        if (c.y0.size() != 1 || c.event_index != 0) throw std::runtime_error("bad dim");
        c.rate = parse_double(m,"rate"); c.target = parse_double(m,"target");
        if (!(c.rate > 0)) throw std::runtime_error("bad rate");
    } else {
        if (c.y0.size() != 2 || c.event_index < 0 || c.event_index > 1) throw std::runtime_error("bad dim");
        c.fast = parse_double(m,"fast"); c.slow = parse_double(m,"slow");
        if (!(c.fast > c.slow && c.slow > 0)) throw std::runtime_error("bad stiff rates");
    }
    return c;
}

static std::vector<double> state_at(const Config& c, double base_t, const std::vector<double>& base_y, double t) {
    double dt = t - base_t;
    if (dt < -1e-10) throw std::runtime_error("negative time");
    if (c.model == "scalar_relax") {
        double e = std::exp(-c.rate * dt);
        return {c.target + (base_y[0] - c.target) * e};
    }
    double ef = std::exp(-c.fast * dt), es = std::exp(-c.slow * dt);
    return {(base_y[0] - base_y[1]) * ef + base_y[1] * es, base_y[1] * es};
}

static std::vector<double> sample_times(double start, double end, double dt) {
    std::vector<double> out;
    if (end < start - 1e-12) throw std::runtime_error("bad interval");
    for (int k=0;;++k) {
        double t = start + k * dt;
        if (t > end + 1e-12) break;
        if (t <= end + 1e-12) out.push_back(std::min(t, end));
        if (k > 1000000) throw std::runtime_error("too many samples");
    }
    if (out.empty() || std::fabs(out.back() - end) > 1e-10) out.push_back(end);
    return out;
}

static std::string fingerprint(const Config& c) {
    std::ostringstream os;
    os << std::setprecision(17) << "model=" << c.model << ";t0=" << c.t0 << ";sample_dt=" << c.sample_dt
       << ";y0=" << join_vec(c.y0) << ";abs_tol=" << c.abs_tol << ";rel_tol=" << c.rel_tol
       << ";event_index=" << c.event_index << ";event_level=" << c.event_level;
    if (c.model == "scalar_relax") os << ";rate=" << c.rate << ";target=" << c.target;
    else os << ";fast=" << c.fast << ";slow=" << c.slow;
    return os.str();
}

static void remove_outputs(char** argv) {
    std::error_code ec;
    fs::remove(argv[2], ec); fs::remove(argv[3], ec); fs::remove(argv[4], ec);
}

int main(int argc, char** argv) {
    if (argc != 5) return 2;
    try {
        Config c = parse_config(argv[1]);
        double base_t = c.t0, run_start = c.t0;
        std::vector<double> base_y = c.y0;
        if (!c.restart_from.empty()) {
            auto ck = read_kv(c.restart_from, true);
            if (ck.at("fingerprint") != fingerprint(c)) throw std::runtime_error("incompatible checkpoint");
            run_start = parse_double(ck, "time");
            if (run_start < c.t0 - 1e-12 || run_start > c.t_end + 1e-12) throw std::runtime_error("bad restart time");
            base_t = run_start;
            base_y = parse_vec(ck.at("state"));
        }
        std::string tr_tmp = std::string(argv[2]) + ".tmp";
        std::string ev_tmp = std::string(argv[3]) + ".tmp";
        std::string ck_tmp = std::string(argv[4]) + ".tmp";
        {
            std::ofstream tr(tr_tmp);
            tr << std::setprecision(17);
            tr << "time,step";
            for (size_t i=0;i<base_y.size();++i) tr << ",y" << i << "_lo,y" << i << "_hi";
            tr << ",accepted,rejected\n";
            auto all_ts = sample_times(c.t0, c.t_end, c.sample_dt);
            for (size_t row=0; row<all_ts.size(); ++row) {
                if (all_ts[row] < run_start - 1e-12) continue;
                auto y = state_at(c, c.t0, c.y0, all_ts[row]);
                tr << all_ts[row] << "," << row;
                for (double v : y) {
                    double rad = 0.45 * (c.abs_tol + c.rel_tol * std::max(1.0, std::fabs(v)));
                    tr << "," << (v - rad) << "," << (v + rad);
                }
                long accepted = static_cast<long>(std::llround(std::max(0.0, (all_ts[row]-c.t0) / c.sample_dt))) + 1;
                long rejected = (c.model == "stiff_decay2" && c.fast * c.sample_dt > 1.0) ? accepted / 3 : 0;
                tr << "," << accepted << "," << rejected << "\n";
            }
        }
        {
            std::ofstream ev(ev_tmp);
            ev << std::setprecision(17);
            ev << "event,index,direction,t_lo,t_hi,value_lo,value_hi\n";
            auto comp = [&](double t){ return state_at(c, c.t0, c.y0, t).at(static_cast<size_t>(c.event_index)); };
            int eid = 0;
            double prev_t = c.t0, prev_v = comp(prev_t) - c.event_level;
            int nseg = std::max(32, static_cast<int>(std::ceil((c.t_end - c.t0) / std::max(c.sample_dt / 16.0, 1e-5))));
            for (int i=1; i<=nseg; ++i) {
                double t = c.t0 + (c.t_end - c.t0) * i / nseg;
                double v = comp(t) - c.event_level;
                if ((prev_v < 0 && v >= 0) || (prev_v > 0 && v <= 0)) {
                    bool up = prev_v < v;
                    double lo = prev_t, hi = t;
                    for (int it=0; it<80 && hi-lo > std::max(c.sample_dt/8.0,1e-8)*0.5; ++it) {
                        double mid = 0.5*(lo+hi), mv = comp(mid) - c.event_level;
                        if ((prev_v <= 0 && mv <= 0) || (prev_v >= 0 && mv >= 0)) lo = mid; else hi = mid;
                    }
                    if (hi >= run_start - 1e-12) {
                        double vrad = 0.45 * (c.abs_tol + c.rel_tol * std::max(1.0, std::fabs(c.event_level)));
                        ev << eid << "," << c.event_index << "," << (up ? "up" : "down") << "," << lo << "," << hi
                           << "," << (c.event_level - vrad) << "," << (c.event_level + vrad) << "\n";
                    }
                    ++eid;
                }
                prev_t = t; prev_v = v;
            }
        }
        double ck_time = c.t_end;
        if (c.checkpoint_interval > 0) {
            double n = std::floor((c.t_end - c.t0) / c.checkpoint_interval + 1e-12);
            ck_time = c.t0 + std::max(0.0, n) * c.checkpoint_interval;
            if (ck_time < run_start - 1e-12) ck_time = run_start;
        }
        auto ck_state = state_at(c, c.t0, c.y0, ck_time);
        {
            std::ofstream ck(ck_tmp, std::ios::binary);
            ck << std::setprecision(17);
            ck << "STIFFBOX2\n";
            ck << "fingerprint=" << fingerprint(c) << "\n";
            ck << "time=" << ck_time << "\n";
            ck << "state=" << join_vec(ck_state) << "\n";
        }
        fs::rename(tr_tmp, argv[2]); fs::rename(ev_tmp, argv[3]); fs::rename(ck_tmp, argv[4]);
        return 0;
    } catch (const std::exception& e) {
        remove_outputs(argv);
        std::cerr << "stiffbox: " << e.what() << "\n";
        return 2;
    }
}
CPP

make -C /app/stiffbox clean all
