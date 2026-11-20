#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr double kWidth = 0.5;
constexpr uint64_t kOffset = 1469598103934665603ull;
constexpr uint64_t kPrime = 1099511628211ull;

struct Config {
  int nodes = 0;
  int steps = 0;
  int checkpoint_step = 0;
  bool resume = false;
  double length = 0.0;
  double dt = 0.0;
  double conductivity = 0.0;
  double heat_capacity = 0.0;
  double latent_heat = 0.0;
  double melt_temperature = 0.0;
  double initial_temperature = 0.0;
  double left_start = 0.0;
  double right_start = 0.0;
  double left_resume = 0.0;
  double right_resume = 0.0;
  std::string forcing_path;
};

struct ForcingRow {
  int step = 0;
  double scale = 1.0;
  double left_offset = 0.0;
  double right_offset = 0.0;
  std::vector<double> source;
};

struct ReportData {
  std::vector<double> temperatures;
  double front = 0.0;
  double enthalpy = 0.0;
  double residual = 0.0;
  double exposure = 0.0;
};

std::string trim(const std::string& s) {
  size_t a = 0;
  size_t b = s.size();
  while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
  return s.substr(a, b - a);
}

double parse_double(const std::string& s) {
  size_t pos = 0;
  double value = std::stod(s, &pos);
  if (pos != s.size() || !std::isfinite(value)) throw std::runtime_error("invalid number");
  return value;
}

int parse_int(const std::string& s) {
  size_t pos = 0;
  long value = std::stol(s, &pos);
  if (pos != s.size() || value < std::numeric_limits<int>::min() || value > std::numeric_limits<int>::max()) throw std::runtime_error("invalid integer");
  return static_cast<int>(value);
}

bool parse_bool(const std::string& s) {
  if (s == "true") return true;
  if (s == "false") return false;
  throw std::runtime_error("invalid boolean");
}

std::string parent_dir(const std::string& path) {
  size_t slash = path.find_last_of('/');
  if (slash == std::string::npos) return ".";
  if (slash == 0) return "/";
  return path.substr(0, slash);
}

std::string resolve_path(const std::string& base, const std::string& value) {
  if (!value.empty() && value[0] == '/') return value;
  return parent_dir(base) + "/" + value;
}

Config read_config(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open config");
  std::map<std::string, std::string> values;
  std::string line;
  while (std::getline(input, line)) {
    line = trim(line);
    if (line.empty() || line[0] == '#') continue;
    size_t eq = line.find('=');
    if (eq == std::string::npos) throw std::runtime_error("invalid config line");
    std::string key = trim(line.substr(0, eq));
    std::string value = trim(line.substr(eq + 1));
    if (key.empty() || value.empty() || values.count(key)) throw std::runtime_error("invalid config key");
    values[key] = value;
  }
  const std::array<std::string, 16> keys{"nodes", "length", "steps", "dt", "conductivity", "heat_capacity", "latent_heat", "melt_temperature", "initial_temperature", "left_start", "right_start", "left_resume", "right_resume", "checkpoint_step", "resume", "forcing_file"};
  for (const auto& key : keys) {
    if (!values.count(key)) throw std::runtime_error("missing config key");
  }
  if (values.size() != keys.size()) throw std::runtime_error("unknown config key");
  Config c;
  c.nodes = parse_int(values["nodes"]);
  c.length = parse_double(values["length"]);
  c.steps = parse_int(values["steps"]);
  c.dt = parse_double(values["dt"]);
  c.conductivity = parse_double(values["conductivity"]);
  c.heat_capacity = parse_double(values["heat_capacity"]);
  c.latent_heat = parse_double(values["latent_heat"]);
  c.melt_temperature = parse_double(values["melt_temperature"]);
  c.initial_temperature = parse_double(values["initial_temperature"]);
  c.left_start = parse_double(values["left_start"]);
  c.right_start = parse_double(values["right_start"]);
  c.left_resume = parse_double(values["left_resume"]);
  c.right_resume = parse_double(values["right_resume"]);
  c.checkpoint_step = parse_int(values["checkpoint_step"]);
  c.resume = parse_bool(values["resume"]);
  c.forcing_path = resolve_path(path, values["forcing_file"]);
  if (c.nodes < 5 || c.nodes > 80 || !(c.length > 0) || c.steps < 0 || !(c.dt > 0) || !(c.conductivity > 0) || !(c.heat_capacity > 0) || c.latent_heat < 0 || c.checkpoint_step < 0 || c.checkpoint_step > c.steps) {
    throw std::runtime_error("config value out of range");
  }
  return c;
}

std::vector<ForcingRow> read_forcing(const Config& c) {
  std::ifstream input(c.forcing_path);
  if (!input) throw std::runtime_error("cannot open forcing file");
  std::vector<ForcingRow> rows(static_cast<size_t>(c.steps + 1));
  std::vector<bool> seen(static_cast<size_t>(c.steps + 1), false);
  std::string line;
  while (std::getline(input, line)) {
    line = trim(line);
    if (line.empty() || line[0] == '#') continue;
    std::istringstream iss(line);
    ForcingRow row;
    if (!(iss >> row.step >> row.scale >> row.left_offset >> row.right_offset)) throw std::runtime_error("invalid forcing row");
    if (row.step < 1 || row.step > c.steps || seen[static_cast<size_t>(row.step)] || !std::isfinite(row.scale) || !std::isfinite(row.left_offset) || !std::isfinite(row.right_offset)) {
      throw std::runtime_error("invalid forcing row");
    }
    row.source.resize(static_cast<size_t>(c.nodes));
    for (double& value : row.source) {
      if (!(iss >> value) || !std::isfinite(value)) throw std::runtime_error("invalid forcing source");
    }
    std::string extra;
    if (iss >> extra) throw std::runtime_error("invalid forcing row");
    rows[static_cast<size_t>(row.step)] = row;
    seen[static_cast<size_t>(row.step)] = true;
  }
  for (int step = 1; step <= c.steps; ++step) {
    if (!seen[static_cast<size_t>(step)]) throw std::runtime_error("missing forcing row");
  }
  return rows;
}

uint64_t fnv_update(uint64_t h, const void* data, size_t n) {
  const auto* p = static_cast<const unsigned char*>(data);
  for (size_t i = 0; i < n; ++i) {
    h ^= p[i];
    h *= kPrime;
  }
  return h;
}

void add_u64(uint64_t& h, uint64_t value) {
  unsigned char bytes[8];
  for (int i = 0; i < 8; ++i) bytes[i] = static_cast<unsigned char>((value >> (8 * i)) & 0xffu);
  h = fnv_update(h, bytes, sizeof(bytes));
}

void add_i32(uint64_t& h, int value) { add_u64(h, static_cast<uint32_t>(value)); }

void add_f64(uint64_t& h, double value) {
  uint64_t bits = 0;
  std::memcpy(&bits, &value, sizeof(bits));
  add_u64(h, bits);
}

uint64_t config_digest(const Config& c, const std::vector<ForcingRow>& forcing) {
  uint64_t h = kOffset;
  add_i32(h, c.nodes);
  add_f64(h, c.length);
  add_f64(h, c.dt);
  add_f64(h, c.conductivity);
  add_f64(h, c.heat_capacity);
  add_f64(h, c.latent_heat);
  add_f64(h, c.melt_temperature);
  add_f64(h, c.initial_temperature);
  add_f64(h, c.left_start);
  add_f64(h, c.right_start);
  add_i32(h, c.checkpoint_step);
  for (int step = 1; step <= c.checkpoint_step; ++step) {
    const auto& row = forcing[static_cast<size_t>(c.checkpoint_step - step + 1)];
    add_i32(h, row.step);
    add_f64(h, row.scale);
    add_f64(h, row.left_offset);
    add_f64(h, row.right_offset);
    for (double value : row.source) add_f64(h, value);
  }
  return h;
}

uint64_t state_digest(int saved, double exposure, double residual, const std::vector<double>& temps) {
  uint64_t h = kOffset;
  add_i32(h, saved);
  add_f64(h, exposure);
  add_f64(h, residual);
  for (double temp : temps) add_f64(h, temp);
  return h;
}

uint64_t file_digest(const std::string& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot digest checkpoint");
  uint64_t h = kOffset;
  char buffer[4096];
  while (input) {
    input.read(buffer, sizeof(buffer));
    h = fnv_update(h, buffer, static_cast<size_t>(input.gcount()));
  }
  return h;
}

std::string hex64(uint64_t value) {
  std::ostringstream out;
  out << std::hex << std::nouppercase << std::setfill('0') << std::setw(16) << value;
  return out.str();
}

double heat_capacity_eff(const Config& c, double temp) {
  return c.heat_capacity + (std::abs(temp - c.melt_temperature) < kWidth ? c.latent_heat / (2.0 * kWidth) : 0.0);
}

double liquid_fraction(const Config& c, double temp) {
  return std::clamp((temp - (c.melt_temperature - kWidth)) / (2.0 * kWidth), 0.0, 1.0);
}

std::vector<double> lumped_masses(const Config& c) {
  double dx = c.length / static_cast<double>(c.nodes - 1);
  std::vector<double> mass(c.nodes, dx);
  mass.front() = 0.5 * dx;
  mass.back() = 0.5 * dx;
  return mass;
}

double advance_one_step(const Config& c, const ForcingRow& forcing, std::vector<double>& temps, int step) {
  const double dx = c.length / static_cast<double>(c.nodes - 1);
  const auto mass = lumped_masses(c);
  const double base_left = (step < c.checkpoint_step) ? c.left_start : c.left_resume;
  const double base_right = (step < c.checkpoint_step) ? c.right_start : c.right_resume;
  const double left = forcing.scale * base_left + forcing.left_offset;
  const double right = forcing.scale * base_right + forcing.right_offset;
  std::vector<double> lower(c.nodes), diag(c.nodes), upper(c.nodes), rhs(c.nodes);
  diag[0] = 1.0;
  rhs[0] = left;
  diag[c.nodes - 1] = 1.0;
  rhs[c.nodes - 1] = right;
  for (int i = 1; i + 1 < c.nodes; ++i) {
    double storage = mass[i] * heat_capacity_eff(c, temps[i]) / c.dt;
    lower[i] = -c.conductivity / dx;
    upper[i] = -c.conductivity / dx;
    diag[i] = storage + 2.0 * c.conductivity / dx;
    rhs[i] = storage * temps[i] + forcing.source[static_cast<size_t>(i)];
  }
  for (int i = 1; i < c.nodes; ++i) {
    double factor = lower[i] / diag[i - 1];
    diag[i] -= factor * upper[i - 1];
    rhs[i] -= factor * rhs[i - 1];
  }
  std::vector<double> next(c.nodes);
  next[c.nodes - 1] = rhs[c.nodes - 1] / diag[c.nodes - 1];
  for (int i = c.nodes - 2; i >= 0; --i) next[i] = (rhs[i] - upper[i] * next[i + 1]) / diag[i];
  double residual = 0.0;
  for (int i = 1; i + 1 < c.nodes; ++i) {
    double r = mass[i] * heat_capacity_eff(c, temps[i]) * (next[i] - temps[i]) / c.dt - c.conductivity / dx * (next[i - 1] - 2.0 * next[i] + next[i + 1]) - mass[i] * forcing.source[static_cast<size_t>(i)];
    residual = std::max(residual, std::abs(r));
  }
  temps.swap(next);
  return residual;
}

double front_position(const Config& c, const std::vector<double>& temps) {
  const double dx = c.length / static_cast<double>(c.nodes - 1);
  if (temps.front() < c.melt_temperature) return 0.0;
  for (int i = 0; i + 1 < c.nodes; ++i) {
    if (temps[i] >= c.melt_temperature && temps[i + 1] < c.melt_temperature) {
      double f = (c.melt_temperature - temps[i]) / (temps[i + 1] - temps[i]);
      return (static_cast<double>(i) + f) * dx;
    }
  }
  return c.length;
}

double enthalpy(const Config& c, const std::vector<double>& temps) {
  const auto mass = lumped_masses(c);
  double total = 0.0;
  const double dx = c.length / static_cast<double>(c.nodes - 1);
  for (int i = 0; i < c.nodes; ++i) total += dx * (c.heat_capacity * temps[i] + c.latent_heat * liquid_fraction(c, temps[i]));
  return total;
}

void write_checkpoint(const std::string& path, const Config& c, const std::vector<ForcingRow>& forcing, int step, double exposure, double residual, const std::vector<double>& temps) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot write checkpoint");
  const char magic[8] = {'K', 'R', 'Y', 'O', 'F', 'E', 'M', '2'};
  uint32_t version = 1;
  uint32_t nodes = static_cast<uint32_t>(c.nodes);
  uint32_t saved = static_cast<uint32_t>(step);
  uint64_t digest = config_digest(c, forcing);
  uint64_t sdigest = state_digest(c.checkpoint_step, exposure, residual, temps);
  output.write(magic, 8);
  output.write(reinterpret_cast<const char*>(&version), sizeof(version));
  output.write(reinterpret_cast<const char*>(&nodes), sizeof(nodes));
  output.write(reinterpret_cast<const char*>(&saved), sizeof(saved));
  output.write(reinterpret_cast<const char*>(&digest), sizeof(digest));
  output.write(reinterpret_cast<const char*>(&sdigest), sizeof(sdigest));
  output.write(reinterpret_cast<const char*>(&exposure), sizeof(exposure));
  output.write(reinterpret_cast<const char*>(&residual), sizeof(residual));
  for (double temp : temps) output.write(reinterpret_cast<const char*>(&temp), sizeof(temp));
  if (!output) throw std::runtime_error("checkpoint write failed");
}

struct CheckpointState {
  int saved = 0;
  double exposure = 0.0;
  double residual = 0.0;
  std::vector<double> temperatures;
};

CheckpointState read_checkpoint(const std::string& path, const Config& c, const std::vector<ForcingRow>& forcing) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot open checkpoint");
  char magic[8];
  uint32_t version = 0, nodes = 0, saved = 0;
  uint64_t digest = 0, sdigest = 0;
  double exposure = 0.0, residual = 0.0;
  input.read(magic, 8);
  input.read(reinterpret_cast<char*>(&version), sizeof(version));
  input.read(reinterpret_cast<char*>(&nodes), sizeof(nodes));
  input.read(reinterpret_cast<char*>(&saved), sizeof(saved));
  input.read(reinterpret_cast<char*>(&digest), sizeof(digest));
  input.read(reinterpret_cast<char*>(&sdigest), sizeof(sdigest));
  input.read(reinterpret_cast<char*>(&exposure), sizeof(exposure));
  input.read(reinterpret_cast<char*>(&residual), sizeof(residual));
  if (!input || std::string(magic, magic + 8) != "KRYOFEM2" || version != 1 || nodes != static_cast<uint32_t>(c.nodes) || saved != static_cast<uint32_t>(c.checkpoint_step) || saved > static_cast<uint32_t>(c.steps) || digest != config_digest(c, forcing)) {
    throw std::runtime_error("incompatible checkpoint");
  }
  std::vector<double> temps(c.nodes);
  for (double& temp : temps) input.read(reinterpret_cast<char*>(&temp), sizeof(temp));
  char extra = 0;
  if (!input || input.read(&extra, 1) || !std::isfinite(exposure) || !std::isfinite(residual) || sdigest != state_digest(static_cast<int>(saved), exposure, residual, temps)) throw std::runtime_error("damaged checkpoint");
  return {static_cast<int>(saved), exposure, residual, temps};
}

ReportData simulate(const Config& c, const std::vector<ForcingRow>& forcing, const std::string& checkpoint_path) {
  std::vector<double> temps(c.nodes, c.initial_temperature);
  int start_step = 0;
  double residual = 0.0;
  double exposure = 0.0;
  if (c.resume) {
    auto loaded = read_checkpoint(checkpoint_path, c, forcing);
    start_step = loaded.saved;
    exposure = loaded.exposure;
    residual = loaded.residual;
    temps = loaded.temperatures;
  } else if (c.checkpoint_step == 0) {
    write_checkpoint(checkpoint_path, c, forcing, 0, exposure, residual, temps);
  }
  for (int step = start_step + 1; step <= c.steps; ++step) {
    residual = std::max(residual, advance_one_step(c, forcing[static_cast<size_t>(step)], temps, step));
    exposure += front_position(c, temps) * c.dt;
    if (!c.resume && step == c.checkpoint_step) write_checkpoint(checkpoint_path, c, forcing, step, exposure, residual, temps);
  }
  if (c.resume || c.checkpoint_step == c.steps) write_checkpoint(checkpoint_path, c, forcing, c.steps, exposure, residual, temps);
  return {temps, front_position(c, temps), enthalpy(c, temps), residual, exposure};
}

void write_report(const std::string& path, const Config& c, const ReportData& report, const std::string& checkpoint_path) {
  std::ofstream output(path, std::ios::trunc);
  if (!output) throw std::runtime_error("cannot write report");
  output << std::setprecision(12) << std::fixed;
  output << "{\n  \"schema\": \"kryofem-report-v1\",\n";
  output << "  \"nodes\": " << c.nodes << ",\n";
  output << "  \"steps\": " << c.steps << ",\n";
  output << "  \"checkpoint_step\": " << c.checkpoint_step << ",\n";
  output << "  \"dx\": " << c.length / static_cast<double>(c.nodes - 1) << ",\n";
  output << "  \"temperatures\": [";
  for (size_t i = 0; i < report.temperatures.size(); ++i) {
    if (i != 0) output << ", ";
    output << report.temperatures[i];
  }
  output << "],\n";
  output << "  \"front_position\": " << report.front << ",\n";
  output << "  \"enthalpy\": " << report.enthalpy << ",\n";
  output << std::scientific << "  \"energy_residual_max\": " << report.residual << std::fixed << ",\n";
  output << "  \"thaw_exposure\": " << report.exposure << ",\n";
  output << "  \"checkpoint_digest\": \"" << hex64(file_digest(checkpoint_path)) << "\"\n}\n";
  if (!output) throw std::runtime_error("report write failed");
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "usage: kryofem <config> <report.json> <checkpoint.bin>\n";
    return 2;
  }
  try {
    Config config = read_config(argv[1]);
    auto forcing = read_forcing(config);
    ReportData report = simulate(config, forcing, argv[3]);
    write_report(argv[2], config, report, argv[3]);
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "kryofem: " << e.what() << "\n";
    return 1;
  }
}
