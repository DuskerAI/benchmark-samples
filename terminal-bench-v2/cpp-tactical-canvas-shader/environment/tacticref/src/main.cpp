#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

struct Rect { int x=0, y=0, w=0, h=0; };
struct Piece { std::string id, kind; Rect r, clip; int value=0, layer=0, order=0, cells=0; };

static bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static Rect intersect_rect(Rect a, Rect b) {
    int x0 = std::max(a.x, b.x), y0 = std::max(a.y, b.y);
    int x1 = std::min(a.x + a.w, b.x + b.w), y1 = std::min(a.y + a.h, b.y + b.h);
    return {x0, y0, std::max(0, x1 - x0), std::max(0, y1 - y0)};
}

static int area(Rect r) { return std::max(0, r.w) * std::max(0, r.h); }

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
    std::vector<Rect> windows;
    std::vector<Piece> pieces;
    std::string line;

    while (std::getline(in, line)) {
        std::istringstream ss(line);
        std::string op;
        ss >> op;
        if (op.empty()) continue;
        if (op == "board") {
            ss >> width >> height;
            windows.clear();
            windows.push_back({0, 0, width, height});
        } else if (op == "time") {
            ss >> t;
            t = std::clamp(t, 0.0, 1.0);
        } else if (op == "select") {
            ss >> sx >> sy;
        } else if (op == "window") {
            Rect r;
            ss >> r.x >> r.y >> r.w >> r.h;
            windows.push_back(r);
        } else if (op == "pop") {
            if (windows.size() > 1) windows.pop_back();
        } else if (op == "zone") {
            Piece p;
            p.kind = "zone";
            ss >> p.id >> p.r.x >> p.r.y >> p.r.w >> p.r.h >> p.value >> p.layer;
            p.clip = windows.empty() ? Rect{0,0,0,0} : windows.back();
            p.order = order++;
            p.cells = area(intersect_rect(p.r, p.clip));
            pieces.push_back(p);
        } else if (op == "unit") {
            Piece p;
            p.kind = "unit";
            int x0, y0, x1, y1;
            ss >> p.id >> x0 >> y0 >> x1 >> y1 >> p.r.w >> p.r.h >> p.value >> p.layer;
            p.r.x = static_cast<int>(std::lround(x0 + (x1 - x0) * t));
            p.r.y = static_cast<int>(std::lround(y0 + (y1 - y0) * t));
            p.clip = windows.empty() ? Rect{0,0,0,0} : windows.back();
            p.order = order++;
            p.cells = area(intersect_rect(p.r, p.clip));
            pieces.push_back(p);
        }
    }

    int zone_total = 0, unit_total = 0;
    const Piece* selected = nullptr;
    for (const auto& p : pieces) {
        if (p.kind == "zone") zone_total += p.cells * p.value;
        else unit_total += p.cells * p.value;
        if (inside(p.r, sx, sy) && inside(p.clip, sx, sy) && (!selected || p.layer > selected->layer)) selected = &p;
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
