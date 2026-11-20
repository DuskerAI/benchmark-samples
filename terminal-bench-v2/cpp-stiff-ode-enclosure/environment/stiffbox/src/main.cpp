#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

static std::vector<double> split_vec(const std::string& s) {
    std::vector<double> out;
    std::stringstream ss(s);
    std::string item;
    while (std::getline(ss, item, ',')) out.push_back(std::stod(item));
    return out;
}

int main(int argc, char** argv) {
    if (argc != 5) return 2;
    std::ifstream in(argv[1]);
    if (!in) return 2;
    std::map<std::string, std::string> cfg;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        auto p = line.find('=');
        if (p == std::string::npos) return 2;
        cfg[line.substr(0, p)] = line.substr(p + 1);
    }
    try {
        auto y = split_vec(cfg.at("y0"));
        double t0 = std::stod(cfg.at("t0"));
        double tend = std::stod(cfg.at("t_end"));
        double dt = std::stod(cfg.at("sample_dt"));
        double fast = cfg.count("fast") ? std::stod(cfg["fast"]) : std::stod(cfg["rate"]);
        std::ofstream tr(argv[2]);
        std::ofstream ev(argv[3]);
        std::ofstream ck(argv[4], std::ios::binary);
        tr << "time,step,y0_lo,y0_hi";
        if (y.size() == 2) tr << ",y1_lo,y1_hi";
        tr << ",accepted,rejected\n";
        // Deliberately simple explicit Euler starter: it is not an enclosure tool.
        double t = t0;
        int step = 0;
        while (t <= tend + 1e-12) {
            tr << std::setprecision(17) << t << "," << step;
            for (double v : y) tr << "," << v << "," << v;
            tr << "," << step << ",0\n";
            if (y.size() == 1) {
                double target = cfg.count("target") ? std::stod(cfg["target"]) : 0.0;
                y[0] += dt * (-fast * (y[0] - target));
            } else {
                double slow = std::stod(cfg["slow"]);
                double y0n = y[0] + dt * (-fast * y[0] + (fast - slow) * y[1]);
                double y1n = y[1] + dt * (-slow * y[1]);
                y = {y0n, y1n};
            }
            t += dt;
            ++step;
        }
        ev << "event,index,direction,t_lo,t_hi,value_lo,value_hi\n";
        ck << "not-a-compatible-checkpoint\n";
    } catch (...) {
        return 2;
    }
    return 0;
}
