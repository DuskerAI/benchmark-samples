#include "notes.hpp"

#include <cassert>
#include <cstdint>
#include <string>
#include <vector>

namespace {

void put32(std::vector<std::uint8_t>& out, std::uint32_t v) {
    for (int i = 0; i < 4; ++i) out.push_back(static_cast<std::uint8_t>((v >> (8 * i)) & 0xff));
}

std::vector<std::uint8_t> note(std::string name, std::uint32_t type, std::vector<std::uint8_t> desc) {
    if (name.empty() || name.back() != '\0') name.push_back('\0');
    std::vector<std::uint8_t> out;
    put32(out, static_cast<std::uint32_t>(name.size()));
    put32(out, static_cast<std::uint32_t>(desc.size()));
    put32(out, type);
    out.insert(out.end(), name.begin(), name.end());
    while (out.size() % 4 != 0) out.push_back(0);
    out.insert(out.end(), desc.begin(), desc.end());
    while (out.size() % 4 != 0) out.push_back(0);
    return out;
}

}  // namespace

int main() {
    auto blob = note("GNU", 3, {0xde, 0xad, 0xbe, 0xef});
    auto notes = parse_note_blob(blob);
    assert(notes.size() == 1);
    assert(notes[0].name == "GNU");
    assert(notes[0].type == 3);
    assert((notes[0].desc == std::vector<std::uint8_t>{0xde, 0xad, 0xbe, 0xef}));

    blob = note("ABC", 0x10, {1, 2, 3, 4});
    auto second = note("GNU", 1, {5, 6, 7, 8});
    blob.insert(blob.end(), second.begin(), second.end());
    notes = parse_note_blob(blob);
    assert(notes.size() == 2);
    assert(notes[0].name == "ABC");
    assert(notes[0].type == 0x10);
    assert((notes[0].desc == std::vector<std::uint8_t>{1, 2, 3, 4}));
    assert(notes[1].name == "GNU");
    assert(notes[1].type == 1);
    assert((notes[1].desc == std::vector<std::uint8_t>{5, 6, 7, 8}));
}
