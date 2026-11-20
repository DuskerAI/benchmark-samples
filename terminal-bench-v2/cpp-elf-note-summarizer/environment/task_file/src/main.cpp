#include "notes.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: note_summary <input-elf> <output-json>\n";
        return 2;
    }

    try {
        std::ifstream in(argv[1], std::ios::binary);
        if (!in) {
            std::cerr << "unable to open input\n";
            return 1;
        }
        std::vector<std::uint8_t> bytes{
            std::istreambuf_iterator<char>(in),
            std::istreambuf_iterator<char>()};

        auto notes = parse_elf_notes(bytes);
        std::ofstream out(argv[2], std::ios::binary);
        if (!out) {
            std::cerr << "unable to open output\n";
            return 1;
        }
        write_summary_json(std::filesystem::path(argv[1]).filename().string(), notes, out);
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }

    return 0;
}
