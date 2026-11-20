#!/bin/bash
set -euo pipefail

cat > /app/tacticref/src/main.cpp <<'CPP'
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

struct Rect { int x=0, y=0, w=0, h=0; };
using Cell = std::pair<int, int>;
using Visible = std::set<Cell>;
struct Piece { std::string id, kind; Rect r; Visible visible; int value=0, layer=0, order=0, cells=0; };

static bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static Visible full_board(int width, int height) {
    Visible cells;
    for (int x = 0; x < width; ++x) for (int y = 0; y < height; ++y) cells.insert({x, y});
    return cells;
}

static Visible keep_inside(const Visible& cells, Rect r) {
    Visible out;
    for (const auto& cell : cells) if (inside(r, cell.first, cell.second)) out.insert(cell);
    return out;
}

static Visible remove_inside(const Visible& cells, Rect r) {
    Visible out;
    for (const auto& cell : cells) if (!inside(r, cell.first, cell.second)) out.insert(cell);
    return out;
}

static Visible shift_cells(const Visible& cells, int dx, int dy, int width, int height) {
    Visible out;
    for (const auto& cell : cells) {
        int nx = cell.first + dx, ny = cell.second + dy;
        if (nx >= 0 && ny >= 0 && nx < width && ny < height) out.insert({nx, ny});
    }
    return out;
}

static Visible wrap_cells(const Visible& cells, int dx, int dy, int width, int height) {
    Visible out;
    for (const auto& cell : cells) {
        int nx = ((cell.first + dx) % width + width) % width;
        int ny = ((cell.second + dy) % height + height) % height;
        out.insert({nx, ny});
    }
    return out;
}

static std::vector<Cell> bresenham(int x0, int y0, int x1, int y1) {
    std::vector<Cell> cells;
    int dx = std::abs(x1 - x0), dy = -std::abs(y1 - y0);
    int sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    int err = dx + dy, x = x0, y = y0;
    while (true) {
        cells.push_back({x, y});
        if (x == x1 && y == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x += sx; }
        if (e2 <= dx) { err += dx; y += sy; }
    }
    return cells;
}

static Visible trace_cells(const Visible& cells, int x0, int y0, int x1, int y1, int radius, int width, int height) {
    Visible mask;
    for (const auto& p : bresenham(x0, y0, x1, y1)) {
        for (int dx = -radius; dx <= radius; ++dx) for (int dy = -radius; dy <= radius; ++dy) {
            int nx = p.first + dx, ny = p.second + dy;
            if (nx >= 0 && ny >= 0 && nx < width && ny < height) mask.insert({nx, ny});
        }
    }
    Visible out;
    for (const auto& cell : cells) if (mask.count(cell)) out.insert(cell);
    return out;
}

static std::vector<Cell> manhattan_offsets(int radius) {
    std::vector<Cell> offsets;
    for (int dx = -radius; dx <= radius; ++dx) for (int dy = -radius; dy <= radius; ++dy) {
        if (std::abs(dx) + std::abs(dy) <= radius) offsets.push_back({dx, dy});
    }
    return offsets;
}

static Visible dilate_cells(const Visible& cells, int radius, int width, int height) {
    Visible out;
    auto offsets = manhattan_offsets(radius);
    for (const auto& cell : cells) for (const auto& off : offsets) {
        int nx = cell.first + off.first, ny = cell.second + off.second;
        if (nx >= 0 && ny >= 0 && nx < width && ny < height) out.insert({nx, ny});
    }
    return out;
}

static Visible erode_cells(const Visible& cells, int radius, int width, int height) {
    Visible out;
    auto offsets = manhattan_offsets(radius);
    for (const auto& cell : cells) {
        bool keep = true;
        for (const auto& off : offsets) {
            int nx = cell.first + off.first, ny = cell.second + off.second;
            if (nx < 0 || ny < 0 || nx >= width || ny >= height || !cells.count({nx, ny})) {
                keep = false;
                break;
            }
        }
        if (keep) out.insert(cell);
    }
    return out;
}

static Visible flood_cells(const Visible& cells, Cell seed) {
    if (!cells.count(seed)) return {};
    Visible seen;
    std::vector<Cell> queue;
    seen.insert(seed);
    queue.push_back(seed);
    for (size_t i = 0; i < queue.size(); ++i) {
        int x = queue[i].first, y = queue[i].second;
        const Cell neighbors[4] = {{x + 1, y}, {x - 1, y}, {x, y + 1}, {x, y - 1}};
        for (const auto& cell : neighbors) {
            if (cells.count(cell) && !seen.count(cell)) {
                seen.insert(cell);
                queue.push_back(cell);
            }
        }
    }
    return seen;
}

static Visible grade_cells(const Visible& cells, int min_count, int max_count, int width, int height) {
    Visible out;
    for (const auto& cell : cells) {
        int count = 0;
        for (int dx = -1; dx <= 1; ++dx) for (int dy = -1; dy <= 1; ++dy) {
            int nx = cell.first + dx, ny = cell.second + dy;
            if (nx >= 0 && ny >= 0 && nx < width && ny < height && cells.count({nx, ny})) ++count;
        }
        if (count >= min_count && count <= max_count) out.insert(cell);
    }
    return out;
}

static std::map<Cell, int> distances(const Visible& cells, Cell seed) {
    std::map<Cell, int> dist;
    if (!cells.count(seed)) return dist;
    std::vector<Cell> queue;
    dist[seed] = 0;
    queue.push_back(seed);
    for (size_t i = 0; i < queue.size(); ++i) {
        int x = queue[i].first, y = queue[i].second;
        const Cell neighbors[4] = {{x + 1, y}, {x - 1, y}, {x, y + 1}, {x, y - 1}};
        for (const auto& cell : neighbors) {
            if (cells.count(cell) && !dist.count(cell)) {
                dist[cell] = dist[queue[i]] + 1;
                queue.push_back(cell);
            }
        }
    }
    return dist;
}

static Visible route_cells(const Visible& cells, Cell start, Cell end, int radius, int width, int height) {
    auto from_start = distances(cells, start);
    if (!from_start.count(end)) return {};
    auto from_end = distances(cells, end);
    int shortest = from_start[end];
    Visible path_cells;
    for (const auto& cell : cells) {
        auto a = from_start.find(cell), b = from_end.find(cell);
        if (a != from_start.end() && b != from_end.end() && a->second + b->second == shortest) path_cells.insert(cell);
    }
    Visible out;
    for (const auto& cell : path_cells) {
        for (int dx = -radius; dx <= radius; ++dx) for (int dy = -radius; dy <= radius; ++dy) {
            int nx = cell.first + dx, ny = cell.second + dy;
            if (std::max(std::abs(dx), std::abs(dy)) <= radius && nx >= 0 && ny >= 0 && nx < width && ny < height && cells.count({nx, ny})) {
                out.insert({nx, ny});
            }
        }
    }
    return out;
}

static Visible anchor_cells(const Visible& cells, const Visible& source, int radius) {
    Visible expanded;
    auto offsets = manhattan_offsets(radius);
    for (const auto& cell : source) for (const auto& off : offsets) expanded.insert({cell.first + off.first, cell.second + off.second});
    Visible out;
    for (const auto& cell : cells) if (expanded.count(cell)) out.insert(cell);
    return out;
}

static std::string quote(const std::string& s) {
    std::string out = "\"";
    for (char ch : s) {
        if (ch == '\\' || ch == '\"') out += '\\';
        out += ch;
    }
    return out + "\"";
}

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    std::ifstream in(argv[1]);
    if (!in) return 3;

    int width = 0, height = 0, sx = -1, sy = -1, order = 0;
    double t = 0.0;
    std::vector<Visible> visibility;
    std::map<std::string, Visible> saved;
    std::map<std::string, Visible> recorded;
    std::vector<Piece> pieces;
    std::string line;

    while (std::getline(in, line)) {
        std::istringstream ss(line);
        std::string op;
        ss >> op;
        if (op.empty()) continue;
        if (op == "board") {
            ss >> width >> height;
            visibility.clear();
            visibility.push_back(full_board(width, height));
            saved.clear();
            recorded.clear();
        } else if (op == "time") {
            ss >> t;
            t = std::clamp(t, 0.0, 1.0);
        } else if (op == "select") {
            ss >> sx >> sy;
        } else if (op == "window") {
            Rect r;
            ss >> r.x >> r.y >> r.w >> r.h;
            visibility.push_back(keep_inside(visibility.back(), r));
        } else if (op == "block") {
            Rect r;
            ss >> r.x >> r.y >> r.w >> r.h;
            visibility.push_back(remove_inside(visibility.back(), r));
        } else if (op == "shift") {
            int dx, dy;
            ss >> dx >> dy;
            visibility.push_back(shift_cells(visibility.back(), dx, dy, width, height));
        } else if (op == "wrap") {
            int dx, dy;
            ss >> dx >> dy;
            visibility.push_back(wrap_cells(visibility.back(), dx, dy, width, height));
        } else if (op == "trace") {
            int x0, y0, x1, y1, radius;
            ss >> x0 >> y0 >> x1 >> y1 >> radius;
            visibility.push_back(trace_cells(visibility.back(), x0, y0, x1, y1, radius, width, height));
        } else if (op == "dilate") {
            int radius;
            ss >> radius;
            visibility.push_back(dilate_cells(visibility.back(), radius, width, height));
        } else if (op == "erode") {
            int radius;
            ss >> radius;
            visibility.push_back(erode_cells(visibility.back(), radius, width, height));
        } else if (op == "flood") {
            int x, y;
            ss >> x >> y;
            visibility.push_back(flood_cells(visibility.back(), {x, y}));
        } else if (op == "grade") {
            int min_count, max_count;
            ss >> min_count >> max_count;
            visibility.push_back(grade_cells(visibility.back(), min_count, max_count, width, height));
        } else if (op == "route") {
            int x0, y0, x1, y1, radius;
            ss >> x0 >> y0 >> x1 >> y1 >> radius;
            visibility.push_back(route_cells(visibility.back(), {x0, y0}, {x1, y1}, radius, width, height));
        } else if (op == "anchor") {
            std::string id;
            int radius;
            ss >> id >> radius;
            auto it = recorded.find(id);
            visibility.push_back(anchor_cells(visibility.back(), it == recorded.end() ? Visible{} : it->second, radius));
        } else if (op == "save") {
            std::string name;
            ss >> name;
            saved[name] = visibility.empty() ? Visible{} : visibility.back();
        } else if (op == "merge") {
            std::string name;
            ss >> name;
            Visible out = visibility.empty() ? Visible{} : visibility.back();
            auto it = saved.find(name);
            if (it != saved.end()) out.insert(it->second.begin(), it->second.end());
            visibility.push_back(out);
        } else if (op == "cross") {
            std::string name;
            ss >> name;
            Visible out;
            auto it = saved.find(name);
            if (it != saved.end() && !visibility.empty()) {
                for (const auto& cell : visibility.back()) if (it->second.count(cell)) out.insert(cell);
            }
            visibility.push_back(out);
        } else if (op == "pop") {
            if (visibility.size() > 1) visibility.pop_back();
        } else if (op == "zone") {
            Piece p;
            p.kind = "zone";
            ss >> p.id >> p.r.x >> p.r.y >> p.r.w >> p.r.h >> p.value >> p.layer;
            p.visible = visibility.empty() ? Visible{} : keep_inside(visibility.back(), p.r);
            p.order = order++;
            p.cells = static_cast<int>(p.visible.size());
            pieces.push_back(p);
            recorded[p.id] = p.visible;
        } else if (op == "unit") {
            Piece p;
            p.kind = "unit";
            int x0, y0, x1, y1;
            ss >> p.id >> x0 >> y0 >> x1 >> y1 >> p.r.w >> p.r.h >> p.value >> p.layer;
            p.r.x = static_cast<int>(std::floor(x0 + (x1 - x0) * t));
            p.r.y = static_cast<int>(std::floor(y0 + (y1 - y0) * t));
            p.visible = visibility.empty() ? Visible{} : keep_inside(visibility.back(), p.r);
            p.order = order++;
            p.cells = static_cast<int>(p.visible.size());
            pieces.push_back(p);
            recorded[p.id] = p.visible;
        }
    }

    int zone_total = 0, unit_total = 0;
    const Piece* selected = nullptr;
    for (const auto& p : pieces) {
        if (p.kind == "zone") zone_total += p.cells * p.value;
        else unit_total += p.cells * p.value;
        if (inside(p.r, sx, sy) && p.visible.count({sx, sy}) &&
            (!selected || p.layer > selected->layer || (p.layer == selected->layer && p.order > selected->order))) {
            selected = &p;
        }
    }

    std::ofstream out(argv[2]);
    out << "{\"board\":{\"width\":" << width << ",\"height\":" << height << "},";
    out << "\"selected\":" << (selected ? quote(selected->id) : "null") << ",";
    out << "\"totals\":{\"zone\":" << zone_total << ",\"unit\":" << unit_total << "},\"pieces\":[";
    for (size_t i = 0; i < pieces.size(); ++i) {
        const auto& p = pieces[i];
        if (i) out << ',';
        out << "{\"id\":" << quote(p.id) << ",\"kind\":" << quote(p.kind)
            << ",\"x\":" << p.r.x << ",\"y\":" << p.r.y << ",\"w\":" << p.r.w << ",\"h\":" << p.r.h
            << ",\"cells\":" << p.cells << ",\"value\":" << p.value << ",\"layer\":" << p.layer << '}';
    }
    out << "]}\n";
    return 0;
}
CPP

/app/tacticref/scripts/build.sh
/app/tacticref/bin/tactics_referee /app/tacticref/turns/smoke.turn /tmp/tacticref_smoke_report.json
echo "Build and smoke test passed"
