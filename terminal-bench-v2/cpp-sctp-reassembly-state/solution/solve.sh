#!/bin/bash
set -euo pipefail

cd /app/task_file

cat > src/reassembler.cpp <<'CPP'
#include "reassembler.hpp"

#include <algorithm>
#include <cctype>
#include <iomanip>
#include <istream>
#include <map>
#include <sstream>
#include <stdexcept>

namespace sctp {

namespace {

std::string lower_hex(std::string s) {
    if (s.size() % 2 != 0) throw std::runtime_error("payload hex has odd length");
    for (char& c : s) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) throw std::runtime_error("payload is not hex");
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

bool has_flag(const std::string& flags, char c) {
    return flags.find(c) != std::string::npos;
}

bool tsn_in_range(uint32_t tsn, uint32_t first, uint32_t last) {
    if (first <= last) return first <= tsn && tsn <= last;
    return tsn >= first || tsn <= last;
}

bool range_overlaps_linear_span(uint32_t first, uint32_t last, uint32_t lo, uint32_t hi) {
    if (first <= last) return lo <= last && first <= hi;
    return range_overlaps_linear_span(first, UINT32_MAX, lo, hi) || range_overlaps_linear_span(0, last, lo, hi);
}

std::string json_string(const std::string& s) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') {
            out << '\\' << static_cast<char>(c);
        } else if (c < 0x20) {
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c) << std::dec;
        } else {
            out << static_cast<char>(c);
        }
    }
    out << '"';
    return out.str();
}

}  // namespace

bool Reassembler::MessageKey::operator<(const MessageKey& other) const {
    if (stream != other.stream) return stream < other.stream;
    if (idata != other.idata) return idata < other.idata;
    if (id != other.id) return id < other.id;
    return unordered < other.unordered;
}

void Reassembler::process(std::istream& input) {
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty()) continue;
        std::istringstream parts(line);
        std::string op;
        parts >> op;
        if (op.empty() || op[0] == '#') continue;

        if (op == "START") {
            uint32_t initial = 0;
            if (!(parts >> initial) || initial == 0) throw std::runtime_error("bad START");
            cumulative_tsn_ = initial - 1;
            started_ = true;
        } else if (op == "DATA") {
            std::string flags;
            Chunk chunk;
            chunk.idata = false;
            if (!(parts >> chunk.tsn >> chunk.stream >> chunk.id >> flags >> chunk.ppid >> chunk.payload_hex)) {
                throw std::runtime_error("bad DATA");
            }
            chunk.begin = has_flag(flags, 'B');
            chunk.end = has_flag(flags, 'E');
            chunk.unordered = has_flag(flags, 'U');
            chunk.payload_hex = lower_hex(chunk.payload_hex);
            accept_chunk(chunk);
        } else if (op == "IDATA") {
            std::string flags;
            Chunk chunk;
            chunk.idata = true;
            if (!(parts >> chunk.tsn >> chunk.stream >> chunk.id >> chunk.fsn >> flags >> chunk.ppid >> chunk.payload_hex)) {
                throw std::runtime_error("bad IDATA");
            }
            chunk.begin = has_flag(flags, 'B');
            chunk.end = has_flag(flags, 'E');
            chunk.unordered = has_flag(flags, 'U');
            chunk.payload_hex = lower_hex(chunk.payload_hex);
            accept_chunk(chunk);
        } else if (op == "ABANDON") {
            uint32_t first = 0;
            uint32_t last = 0;
            if (!(parts >> first >> last)) throw std::runtime_error("bad ABANDON");
            abandon(first, last);
        } else if (op == "RESET") {
            uint16_t stream = 0;
            uint32_t next_ssn = 0;
            uint32_t next_mid = 0;
            if (!(parts >> stream >> next_ssn >> next_mid)) throw std::runtime_error("bad RESET");
            reset(stream, next_ssn, next_mid);
        } else {
            throw std::runtime_error("unknown record");
        }
        advance_and_deliver();
    }
}

void Reassembler::accept_chunk(const Chunk& chunk) {
    if (!started_) {
        if (chunk.tsn == 0) throw std::runtime_error("bad TSN");
        cumulative_tsn_ = chunk.tsn - 1;
        started_ = true;
    }
    if (chunks_by_tsn_.count(chunk.tsn) || abandoned_tsns_.count(chunk.tsn)) return;
    chunks_by_tsn_[chunk.tsn] = chunk;
    if (!chunk.unordered) {
        uint32_t next = chunk.idata ? streams_[chunk.stream].next_mid : streams_[chunk.stream].next_ssn;
        if (chunk.id < next) return;
    }
    MessageKey key{chunk.idata, chunk.stream, chunk.id, chunk.unordered};
    auto& message = messages_[key];
    message.key = key;
    if (chunk.idata) {
        auto same_fsn = std::find_if(message.chunks.begin(), message.chunks.end(), [&](const Chunk& existing) {
            return existing.fsn == chunk.fsn;
        });
        if (same_fsn != message.chunks.end()) {
            if (chunk.tsn < same_fsn->tsn) *same_fsn = chunk;
            return;
        }
    }
    message.chunks.push_back(chunk);
}

void Reassembler::abandon(uint32_t first, uint32_t last) {
    for (uint32_t tsn = first;; ++tsn) {
        abandoned_tsns_.insert(tsn);
        auto chunk_it = chunks_by_tsn_.find(tsn);
        if (chunk_it != chunks_by_tsn_.end()) {
            MessageKey key{chunk_it->second.idata, chunk_it->second.stream, chunk_it->second.id, chunk_it->second.unordered};
            auto message_it = messages_.find(key);
            if (message_it != messages_.end() && !message_complete(message_it->second)) message_it->second.failed = true;
        }
        if (tsn == last) break;
    }
    for (auto& [key, message] : messages_) {
        (void)key;
        if (message_complete(message) || message.chunks.empty()) continue;
        uint32_t min_seen = message.chunks.front().tsn;
        uint32_t max_seen = message.chunks.front().tsn;
        for (const auto& chunk : message.chunks) {
            min_seen = std::min(min_seen, chunk.tsn);
            max_seen = std::max(max_seen, chunk.tsn);
        }
        if (range_overlaps_linear_span(first, last, min_seen, max_seen) || tsn_in_range(max_seen + 1, first, last)) {
            message.failed = true;
        }
    }
}

void Reassembler::reset(uint16_t stream, uint32_t next_ssn, uint32_t next_mid) {
    for (auto it = messages_.begin(); it != messages_.end();) {
        if (it->first.stream == stream) it = messages_.erase(it);
        else ++it;
    }
    streams_[stream].next_ssn = next_ssn;
    streams_[stream].next_mid = next_mid;
    Event event;
    event.type = "reset";
    event.stream = stream;
    event.next_ssn = next_ssn;
    event.next_mid = next_mid;
    events_.push_back(event);
}

bool Reassembler::message_complete(const Message& message) const {
    if (message.chunks.empty() || message.failed) return false;
    if (!message.key.idata) {
        bool saw_begin = false;
        bool saw_end = false;
        for (const auto& chunk : message.chunks) {
            saw_begin = saw_begin || chunk.begin;
            saw_end = saw_end || chunk.end;
        }
        return saw_begin && saw_end;
    }

    bool begin_at_zero = false;
    bool saw_end = false;
    uint32_t end_fsn = 0;
    std::set<uint32_t> fsns;
    for (const auto& chunk : message.chunks) {
        fsns.insert(chunk.fsn);
        if (chunk.begin && chunk.fsn == 0) begin_at_zero = true;
        if (chunk.end) {
            saw_end = true;
            end_fsn = chunk.fsn;
        }
    }
    if (!begin_at_zero || !saw_end) return false;
    for (uint32_t fsn = 0; fsn <= end_fsn; ++fsn) {
        if (!fsns.count(fsn)) return false;
    }
    return true;
}

bool Reassembler::message_committed(const Message& message) const {
    for (const auto& chunk : message.chunks) {
        if (chunk.tsn > cumulative_tsn_) return false;
    }
    return true;
}

Event Reassembler::make_delivery(const Message& message) const {
    std::vector<Chunk> pieces = message.chunks;
    std::sort(pieces.begin(), pieces.end(), [](const Chunk& a, const Chunk& b) {
        if (a.idata != b.idata) return a.idata < b.idata;
        if (a.idata && a.fsn != b.fsn) return a.fsn < b.fsn;
        return a.tsn < b.tsn;
    });
    Event event;
    event.type = "deliver";
    event.format = message.key.idata ? "idata" : "data";
    event.stream = message.key.stream;
    event.id = message.key.id;
    event.ordered = !message.key.unordered;
    event.ppid = 0;
    event.first_tsn = pieces.front().tsn;
    event.last_tsn = pieces.front().tsn;
    for (const auto& chunk : pieces) {
        event.first_tsn = std::min(event.first_tsn, chunk.tsn);
        event.last_tsn = std::max(event.last_tsn, chunk.tsn);
        if (chunk.end) event.ppid = chunk.ppid;
        event.payload_hex += chunk.payload_hex;
    }
    return event;
}

void Reassembler::emit_skip(const Message& message) {
    Event event;
    event.type = "skip";
    event.format = message.key.idata ? "idata" : "data";
    event.stream = message.key.stream;
    event.id = message.key.id;
    event.reason = "abandoned";
    events_.push_back(event);
}

void Reassembler::advance_and_deliver() {
    auto min_tsn_for = [](const Message& message) {
        uint32_t best = message.chunks.front().tsn;
        for (const auto& chunk : message.chunks) best = std::min(best, chunk.tsn);
        return best;
    };

    while (chunks_by_tsn_.count(cumulative_tsn_ + 1) || abandoned_tsns_.count(cumulative_tsn_ + 1)) {
        ++cumulative_tsn_;
    }

    bool changed = true;
    while (changed) {
        changed = false;

        for (auto it = messages_.begin(); it != messages_.end();) {
            const auto& key = it->first;
            if (key.unordered && it->second.failed) {
                it = messages_.erase(it);
                changed = true;
                continue;
            }
            if (!key.unordered) {
                uint32_t next = key.idata ? streams_[key.stream].next_mid : streams_[key.stream].next_ssn;
                if (key.id < next && (it->second.failed || message_complete(it->second))) {
                    it = messages_.erase(it);
                    changed = true;
                    continue;
                }
            }
            ++it;
        }

        struct Candidate {
            bool skip = false;
            MessageKey key;
        };
        std::vector<Candidate> candidates;

        for (const auto& [key, message] : messages_) {
            if (!key.unordered && message.failed) {
                uint32_t next = key.idata ? streams_[key.stream].next_mid : streams_[key.stream].next_ssn;
                if (key.id == next) candidates.push_back(Candidate{true, key});
            }
        }
        for (const auto& [key, message] : messages_) {
            if (!message_complete(message) || !message_committed(message)) continue;
            uint32_t next = key.idata ? streams_[key.stream].next_mid : streams_[key.stream].next_ssn;
            if (key.unordered || key.id == next) candidates.push_back(Candidate{false, key});
        }
        std::sort(candidates.begin(), candidates.end(), [&](const Candidate& ca, const Candidate& cb) {
            const auto& a = ca.key;
            const auto& b = cb.key;
            const auto& ma = messages_[a];
            const auto& mb = messages_[b];
            uint32_t tsa = min_tsn_for(ma);
            uint32_t tsb = min_tsn_for(mb);
            if (tsa != tsb) return tsa < tsb;
            if (a.stream != b.stream) return a.stream < b.stream;
            if (a.idata != b.idata) return a.idata < b.idata;
            if (a.id != b.id) return a.id < b.id;
            if (a.unordered != b.unordered) return a.unordered > b.unordered;
            return !ca.skip && cb.skip;
        });
        if (!candidates.empty()) {
            auto candidate = candidates.front();
            auto key = candidate.key;
            auto it = messages_.find(key);
            if (it != messages_.end()) {
                if (candidate.skip) {
                    emit_skip(it->second);
                    if (key.idata) streams_[key.stream].next_mid++;
                    else streams_[key.stream].next_ssn++;
                } else {
                    events_.push_back(make_delivery(it->second));
                    if (!key.unordered) {
                        if (key.idata) streams_[key.stream].next_mid++;
                        else streams_[key.stream].next_ssn++;
                    }
                }
                messages_.erase(it);
                changed = true;
                continue;
            }
        }
    }
}

void Reassembler::write_json(std::ostream& output) const {
    output << "{\n  \"events\": [\n";
    for (size_t i = 0; i < events_.size(); ++i) {
        const auto& event = events_[i];
        output << "    {\"type\":" << json_string(event.type);
        if (event.type == "deliver") {
            output << ",\"format\":" << json_string(event.format)
                   << ",\"stream\":" << event.stream
                   << ",\"id\":" << event.id
                   << ",\"ordered\":" << (event.ordered ? "true" : "false")
                   << ",\"ppid\":" << event.ppid
                   << ",\"first_tsn\":" << event.first_tsn
                   << ",\"last_tsn\":" << event.last_tsn
                   << ",\"payload_hex\":" << json_string(event.payload_hex);
        } else if (event.type == "skip") {
            output << ",\"format\":" << json_string(event.format)
                   << ",\"stream\":" << event.stream
                   << ",\"id\":" << event.id
                   << ",\"reason\":" << json_string(event.reason);
        } else if (event.type == "reset") {
            output << ",\"stream\":" << event.stream
                   << ",\"next_ssn\":" << event.next_ssn
                   << ",\"next_mid\":" << event.next_mid;
        }
        output << "}" << (i + 1 == events_.size() ? "" : ",") << "\n";
    }
    size_t buffered = 0;
    for (const auto& [key, message] : messages_) {
        (void)key;
        buffered += message.chunks.size();
    }
    output << "  ],\n  \"state\": {\"cumulative_tsn\":" << cumulative_tsn_
           << ",\"buffered_chunks\":" << buffered
           << ",\"pending_messages\":" << messages_.size() << "}\n}\n";
}

}  // namespace sctp
CPP

make clean
make
./build/sctp_replay examples/smoke.trace /tmp/sctp_smoke.json
