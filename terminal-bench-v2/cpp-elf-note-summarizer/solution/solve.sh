#!/bin/bash
set -euo pipefail

cd /app/task_file

python3 - <<'PY'
from pathlib import Path

Path("src/notes.cpp").write_text(r'''#include "notes.hpp"

#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace {

constexpr std::uint32_t PT_NOTE = 4;
constexpr std::uint16_t PN_XNUM = 0xffff;

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
    if (off + 8 > b.size()) throw std::runtime_error("short read64");
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<std::uint64_t>(b[off + static_cast<std::size_t>(i)]) << (8 * i);
    }
    return v;
}

std::size_t align4(std::size_t value) {
    return (value + 3U) & ~std::size_t{3U};
}

std::string trim_nul(std::string s) {
    while (!s.empty() && s.back() == '\0') {
        s.pop_back();
    }
    return s;
}

std::string owner_string(std::string s) {
    const auto pos = s.find('\0');
    if (pos != std::string::npos) {
        s.resize(pos);
    }
    return s;
}

std::string fixed_string(const std::vector<std::uint8_t>& d, std::size_t off, std::size_t len) {
    std::size_t end = off;
    while (end < off + len && d[end] != 0) {
        ++end;
    }
    return std::string(reinterpret_cast<const char*>(d.data() + off), end - off);
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

std::int32_t desc_i32(const std::vector<std::uint8_t>& d, std::size_t off) {
    return static_cast<std::int32_t>(desc32(d, off));
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

struct FileMapping {
    std::uint64_t start;
    std::uint64_t end;
    std::uint64_t offset;
    std::string path;
};

struct SignalInfo {
    std::int32_t signo;
    std::int32_t error;
    std::int32_t code;
};

struct ProcessInfo {
    std::uint32_t pid;
    std::uint32_t ppid;
    std::string name;
    std::string state;
};

bool decode_file_note(const std::vector<std::uint8_t>& d, std::vector<FileMapping>& out) {
    if (d.size() < 16) {
        return false;
    }
    const auto count64 = desc64(d, 0);
    const auto page_size = desc64(d, 8);
    if (count64 > static_cast<std::uint64_t>((d.size() - 16) / 24)) {
        return false;
    }
    const auto count = static_cast<std::size_t>(count64);
    if (count == 0) {
        return d.size() == 16;
    }
    if (page_size == 0) {
        return false;
    }
    std::size_t names = 16 + count * 24;
    std::vector<FileMapping> parsed;
    parsed.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        const std::size_t row = 16 + i * 24;
        const auto start = desc64(d, row);
        const auto end = desc64(d, row + 8);
        const auto pages = desc64(d, row + 16);
        if (start > end) {
            return false;
        }
        if (page_size != 0 && pages > std::numeric_limits<std::uint64_t>::max() / page_size) {
            return false;
        }
        std::size_t nul = names;
        while (nul < d.size() && d[nul] != 0) {
            ++nul;
        }
        if (nul == d.size()) {
            return false;
        }
        std::string path(reinterpret_cast<const char*>(d.data() + names), nul - names);
        parsed.push_back({start, end, pages * page_size, path});
        names = nul + 1;
    }
    if (names != d.size()) {
        return false;
    }
    out.insert(out.end(), parsed.begin(), parsed.end());
    return true;
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

        const std::size_t name_start = off;
        const std::size_t desc_start = name_start + align4(namesz);
        const std::size_t next = desc_start + align4(descsz);
        if (desc_start < name_start || next < desc_start || next > blob.size()) {
            break;
        }
        if (name_start + namesz > blob.size() || desc_start + descsz > blob.size()) {
            break;
        }

        std::string name(reinterpret_cast<const char*>(blob.data() + name_start), namesz);
        std::vector<std::uint8_t> desc(blob.begin() + static_cast<std::ptrdiff_t>(desc_start),
                                      blob.begin() + static_cast<std::ptrdiff_t>(desc_start + descsz));
        notes.push_back({owner_string(name), type, std::move(desc)});
        off = next;
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
    const auto ehsize = read16(file, 52);
    const auto phentsize = read16(file, 54);
    std::uint64_t phnum = read16(file, 56);
    if (ehsize < 64 || phentsize < 56) {
        throw std::runtime_error("unsupported ELF header layout");
    }
    if (phnum == PN_XNUM) {
        const auto shoff = read64(file, 40);
        const auto shentsize = read16(file, 58);
        if (shentsize < 64 || shoff > file.size() || file.size() - static_cast<std::size_t>(shoff) < 64) {
            phnum = 0;
        } else {
            phnum = read32(file, static_cast<std::size_t>(shoff) + 44);
        }
    }

    std::vector<NoteRecord> result;
    for (std::uint64_t i = 0; i < phnum; ++i) {
        if (phoff > static_cast<std::uint64_t>(file.size())) {
            break;
        }
        const std::uint64_t poff64 = phoff + i * static_cast<std::uint64_t>(phentsize);
        if (poff64 < phoff || poff64 > static_cast<std::uint64_t>(file.size())) {
            break;
        }
        const std::size_t poff = static_cast<std::size_t>(poff64);
        if (poff + 56 > file.size()) {
            break;
        }
        if (read32(file, poff) != PT_NOTE) {
            continue;
        }
        const auto p_offset = static_cast<std::size_t>(read64(file, poff + 8));
        const auto p_filesz = static_cast<std::size_t>(read64(file, poff + 32));
        if (p_offset > file.size() || p_filesz > file.size() - p_offset) {
            continue;
        }
        std::vector<std::uint8_t> blob(file.begin() + static_cast<std::ptrdiff_t>(p_offset),
                                      file.begin() + static_cast<std::ptrdiff_t>(p_offset + p_filesz));
        auto notes = parse_note_blob(blob);
        result.insert(result.end(), notes.begin(), notes.end());
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
    std::vector<SignalInfo> signals;
    std::vector<ProcessInfo> processes;
    std::vector<Aux> auxv;
    std::vector<FileMapping> mapped_files;
    std::vector<const NoteRecord*> unknown;

    for (const auto& n : notes) {
        if (n.name == "GNU" && n.type == 3) {
            build_ids.push_back(hex_bytes(n.desc));
        } else if (n.name == "CORE" && n.type == 1 && n.desc.size() == 24) {
            std::string comm(reinterpret_cast<const char*>(n.desc.data() + 8), 16);
            threads.push_back({desc32(n.desc, 0), desc32(n.desc, 4), trim_nul(comm)});
        } else if (n.name == "CORE" && n.type == 0x53494749) {
            if (n.desc.size() == 12) {
                signals.push_back({desc_i32(n.desc, 0), desc_i32(n.desc, 4), desc_i32(n.desc, 8)});
            } else {
                unknown.push_back(&n);
            }
        } else if (n.name == "CORE" && n.type == 3) {
            if (n.desc.size() == 40) {
                processes.push_back({
                    desc32(n.desc, 0),
                    desc32(n.desc, 4),
                    fixed_string(n.desc, 8, 16),
                    fixed_string(n.desc, 24, 16),
                });
            } else {
                unknown.push_back(&n);
            }
        } else if (n.name == "CORE" && n.type == 6) {
            std::vector<Aux> parsed;
            bool terminated = false;
            for (std::size_t off = 0; off + 16 <= n.desc.size() && n.desc.size() % 16 == 0; off += 16) {
                const auto type = desc64(n.desc, off);
                const auto value = desc64(n.desc, off + 8);
                if (type == 0) {
                    terminated = (off + 16 == n.desc.size());
                    break;
                }
                parsed.push_back({aux_name(type), hex_value(value)});
            }
            if (terminated) {
                auxv.insert(auxv.end(), parsed.begin(), parsed.end());
            } else {
                unknown.push_back(&n);
            }
        } else if (n.name == "CORE" && n.type == 0x46494c45) {
            if (!decode_file_note(n.desc, mapped_files)) {
                unknown.push_back(&n);
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
    out << "  \"signals\": [";
    for (std::size_t i = 0; i < signals.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"signo\": " << signals[i].signo << ", \"errno\": " << signals[i].error
            << ", \"code\": " << signals[i].code << " }";
    }
    out << "],\n";
    out << "  \"processes\": [";
    for (std::size_t i = 0; i < processes.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"pid\": " << processes[i].pid << ", \"ppid\": " << processes[i].ppid
            << ", \"name\": \"" << json_escape(processes[i].name) << "\", \"state\": \""
            << json_escape(processes[i].state) << "\" }";
    }
    out << "],\n";
    out << "  \"auxv\": [";
    for (std::size_t i = 0; i < auxv.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"type\": \"" << auxv[i].type << "\", \"value\": \"" << auxv[i].value << "\" }";
    }
    out << "],\n";
    out << "  \"mapped_files\": [";
    for (std::size_t i = 0; i < mapped_files.size(); ++i) {
        if (i) out << ", ";
        out << "{ \"start\": \"" << hex_value(mapped_files[i].start) << "\", \"end\": \""
            << hex_value(mapped_files[i].end) << "\", \"offset\": \""
            << hex_value(mapped_files[i].offset) << "\", \"path\": \""
            << json_escape(mapped_files[i].path) << "\" }";
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
''')
PY

make clean test
make all
