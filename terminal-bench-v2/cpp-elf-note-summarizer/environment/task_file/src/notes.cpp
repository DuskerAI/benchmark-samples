#include "notes.hpp"

#include <algorithm>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {

constexpr std::uint32_t PT_NOTE = 4;

std::uint16_t read16(const std::vector<std::uint8_t>& b, std::size_t off) {
    if (off + 2 > b.size()) throw std::runtime_error("short read16");
    return static_cast<std::uint16_t>(b[off] | (b[off + 1] << 8));
}

std::uint32_t read32(const std::vector<std::uint8_t>& b, std::size_t off) {
    if (off + 4 > b.size()) throw std::runtime_error("short read32");
    return static_cast<std::uint32_t>(b[off])
        | (static_cast<std::uint32_t>(b[off + 1]) << 8)
        | (static_cast<std::uint32_t>(b[off + 2]) << 16)
        | (static_cast<std::uint32_t>(b[off + 3]) << 24);
}

std::uint64_t read64(const std::vector<std::uint8_t>& b, std::size_t off) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        if (off + static_cast<std::size_t>(i) >= b.size()) {
            throw std::runtime_error("short read64");
        }
        v |= static_cast<std::uint64_t>(b[off + static_cast<std::size_t>(i)]) << (8 * i);
    }
    return v;
}

std::string trim_nul(std::string s) {
    while (!s.empty() && s.back() == '\0') {
        s.pop_back();
    }
    return s;
}

std::string json_escape(const std::string& s) {
    std::ostringstream out;
    for (unsigned char ch : s) {
        switch (ch) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(ch) << std::dec << std::setfill(' ');
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

std::string hex_bytes(const std::vector<std::uint8_t>& b) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (std::uint8_t byte : b) {
        out << std::setw(2) << static_cast<unsigned>(byte);
    }
    return out.str();
}

std::string hex_value(std::uint64_t v) {
    std::ostringstream out;
    out << "0x" << std::hex << std::nouppercase << v;
    return out.str();
}

std::uint32_t desc32(const std::vector<std::uint8_t>& d, std::size_t off) {
    return static_cast<std::uint32_t>(d[off])
        | (static_cast<std::uint32_t>(d[off + 1]) << 8)
        | (static_cast<std::uint32_t>(d[off + 2]) << 16)
        | (static_cast<std::uint32_t>(d[off + 3]) << 24);
}

std::uint64_t desc64(const std::vector<std::uint8_t>& d, std::size_t off) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<std::uint64_t>(d[off + static_cast<std::size_t>(i)]) << (8 * i);
    }
    return v;
}

std::string aux_name(std::uint64_t type) {
    switch (type) {
        case 3: return "AT_PHDR";
        case 7: return "AT_BASE";
        case 9: return "AT_ENTRY";
        case 25: return "AT_RANDOM";
        default: return std::to_string(type);
    }
}

}  // namespace

std::vector<NoteRecord> parse_note_blob(const std::vector<std::uint8_t>& blob) {
    std::vector<NoteRecord> notes;
    std::size_t off = 0;
    while (off + 12 <= blob.size()) {
        const auto namesz = read32(blob, off);
        const auto descsz = read32(blob, off + 4);
        const auto type = read32(blob, off + 8);
        off += 12;
        if (off + namesz + descsz > blob.size()) {
            break;
        }
        std::string name(reinterpret_cast<const char*>(blob.data() + off), namesz);
        off += namesz;
        std::vector<std::uint8_t> desc(blob.begin() + static_cast<std::ptrdiff_t>(off),
                                      blob.begin() + static_cast<std::ptrdiff_t>(off + descsz));
        off += descsz;
        notes.push_back({trim_nul(name), type, std::move(desc)});
    }
    return notes;
}

std::vector<NoteRecord> parse_elf_notes(const std::vector<std::uint8_t>& file) {
    if (file.size() < 64 || file[0] != 0x7f || file[1] != 'E' || file[2] != 'L' || file[3] != 'F') {
        throw std::runtime_error("not an ELF file");
    }
    if (file[4] != 2 || file[5] != 1) {
        throw std::runtime_error("only ELF64 little-endian is supported");
    }

    const auto phoff = read64(file, 32);
    const auto phentsize = read16(file, 54);
    const auto phnum = read16(file, 56);
    std::vector<NoteRecord> result;
    for (std::uint16_t i = 0; i < phnum; ++i) {
        const std::size_t poff = static_cast<std::size_t>(phoff) + static_cast<std::size_t>(i) * phentsize;
        if (poff + 56 > file.size()) {
            break;
        }
        const auto p_type = read32(file, poff);
        if (p_type != PT_NOTE) {
            continue;
        }
        const auto p_offset = static_cast<std::size_t>(read64(file, poff + 8));
        const auto p_filesz = static_cast<std::size_t>(read64(file, poff + 32));
        if (p_offset + p_filesz > file.size()) {
            continue;
        }
        std::vector<std::uint8_t> blob(file.begin() + static_cast<std::ptrdiff_t>(p_offset),
                                      file.begin() + static_cast<std::ptrdiff_t>(p_offset + p_filesz));
        auto notes = parse_note_blob(blob);
        result.insert(result.end(), notes.begin(), notes.end());
        break;
    }
    return result;
}

void write_summary_json(
    const std::string& input_name,
    const std::vector<NoteRecord>& notes,
    std::ostream& out) {
    struct Thread {
        std::uint32_t pid;
        std::uint32_t signal;
        std::string comm;
    };
    struct Aux {
        std::string type;
        std::string value;
    };

    std::vector<std::string> build_ids;
    std::vector<Thread> threads;
    std::vector<Aux> auxv;
    std::vector<const NoteRecord*> unknown;

    for (const auto& n : notes) {
        if (n.name == "GNU" && n.type == 3) {
            build_ids.push_back(hex_bytes(n.desc));
        } else if (n.name == "CORE" && n.type == 1 && n.desc.size() >= 24) {
            std::string comm(reinterpret_cast<const char*>(n.desc.data() + 8), 16);
            threads.push_back({desc32(n.desc, 0), desc32(n.desc, 4), trim_nul(comm)});
        } else if (n.name == "CORE" && n.type == 6) {
            for (std::size_t off = 0; off + 16 <= n.desc.size(); off += 16) {
                auto type = desc64(n.desc, off);
                auto value = desc64(n.desc, off + 8);
                if (type == 0) break;
                auxv.push_back({aux_name(type), hex_value(value)});
            }
        } else {
            unknown.push_back(&n);
        }
    }

    out << "{\n";
    out << "  \"input\": \"" << json_escape(input_name) << "\",\n";
    out << "  \"note_count\": " << notes.size() << ",\n";
    out << "  \"gnu_build_ids\": [";
    for (std::size_t i = 0; i < build_ids.size(); ++i) {
        if (i) out << ", ";
        out << "\"" << build_ids[i] << "\"";
    }
    out << "],\n";
    out << "  \"threads\": [";
    for (std::size_t i = 0; i < threads.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"pid\": " << threads[i].pid << ", \"signal\": " << threads[i].signal
            << ", \"comm\": \"" << json_escape(threads[i].comm) << "\" }";
    }
    out << "],\n";
    out << "  \"auxv\": [";
    for (std::size_t i = 0; i < auxv.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"type\": \"" << auxv[i].type << "\", \"value\": \"" << auxv[i].value << "\" }";
    }
    out << "],\n";
    out << "  \"unknown_notes\": [";
    for (std::size_t i = 0; i < unknown.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"name\": \"" << json_escape(unknown[i]->name) << "\", \"type\": "
            << unknown[i]->type << ", \"desc_size\": " << unknown[i]->desc.size() << " }";
    }
    out << "]\n";
    out << "}\n";
}
