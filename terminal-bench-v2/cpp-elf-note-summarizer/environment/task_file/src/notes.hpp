#pragma once

#include <cstdint>
#include <iosfwd>
#include <string>
#include <vector>

struct NoteRecord {
    std::string name;
    std::uint32_t type;
    std::vector<std::uint8_t> desc;
};

std::vector<NoteRecord> parse_note_blob(const std::vector<std::uint8_t>& blob);
std::vector<NoteRecord> parse_elf_notes(const std::vector<std::uint8_t>& file);
void write_summary_json(
    const std::string& input_name,
    const std::vector<NoteRecord>& notes,
    std::ostream& out);
