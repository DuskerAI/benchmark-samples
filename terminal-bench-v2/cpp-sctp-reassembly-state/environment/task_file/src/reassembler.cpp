#include "reassembler.hpp"

#include <algorithm>
#include <cctype>
#include <iomanip>
#include <istream>
#include <sstream>
#include <stdexcept>

namespace sctp {

namespace {

std::string lower_hex(std::string s) {
    if (s.size() % 2 != 0) {
        throw std::runtime_error("payload hex has odd length");
    }
    for (char& c : s) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) {
            throw std::runtime_error("payload is not hex");
        }
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

bool has_flag(const std::string& flags, char c) {
    return flags.find(c) != std::string::npos;
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
        if (line.empty() || line[0] == '#') {
            continue;
        }
        std::istringstream parts(line);
        std::string op;
        parts >> op;
        if (op == "START") {
            uint32_t initial = 0;
            parts >> initial;
            cumulative_tsn_ = initial - 1;
            started_ = true;
        } else if (op == "DATA") {
            std::string flags;
            Chunk chunk;
            chunk.idata = false;
            parts >> chunk.tsn >> chunk.stream >> chunk.id >> flags >> chunk.ppid >> chunk.payload_hex;
            chunk.begin = has_flag(flags, 'B');
            chunk.end = has_flag(flags, 'E');
            chunk.unordered = has_flag(flags, 'U');
            chunk.payload_hex = lower_hex(chunk.payload_hex);
            accept_chunk(chunk);
        } else if (op == "IDATA") {
            std::string flags;
            Chunk chunk;
            chunk.idata = true;
            parts >> chunk.tsn >> chunk.stream >> chunk.id >> chunk.fsn >> flags >> chunk.ppid >> chunk.payload_hex;
            chunk.begin = has_flag(flags, 'B');
            chunk.end = has_flag(flags, 'E');
            chunk.unordered = has_flag(flags, 'U');
            chunk.payload_hex = lower_hex(chunk.payload_hex);
            accept_chunk(chunk);
        } else if (op == "ABANDON") {
            uint32_t first = 0;
            uint32_t last = 0;
            parts >> first >> last;
            abandon(first, last);
        } else if (op == "RESET") {
            uint16_t stream = 0;
            uint32_t next_ssn = 0;
            uint32_t next_mid = 0;
            parts >> stream >> next_ssn >> next_mid;
            reset(stream, next_ssn, next_mid);
        } else {
            throw std::runtime_error("unknown record");
        }
        advance_and_deliver();
    }
}

void Reassembler::accept_chunk(const Chunk& chunk) {
    if (!started_) {
        cumulative_tsn_ = chunk.tsn - 1;
        started_ = true;
    }
    chunks_by_tsn_[chunk.tsn] = chunk;
    MessageKey key{chunk.idata, chunk.stream, chunk.id, chunk.unordered};
    auto& message = messages_[key];
    message.key = key;
    message.chunks.push_back(chunk);
}

void Reassembler::abandon(uint32_t first, uint32_t last) {
    if (last < first) return;
    for (uint32_t tsn = first; tsn <= last; ++tsn) {
        abandoned_tsns_.insert(tsn);
    }
}

void Reassembler::reset(uint16_t stream, uint32_t next_ssn, uint32_t next_mid) {
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
    if (message.chunks.empty()) return false;
    bool saw_begin = false;
    bool saw_end = false;
    for (const auto& chunk : message.chunks) {
        saw_begin = saw_begin || chunk.begin;
        saw_end = saw_end || chunk.end;
    }
    return saw_begin && saw_end;
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
        return a.tsn < b.tsn;
    });
    Event event;
    event.type = "deliver";
    event.format = message.key.idata ? "idata" : "data";
    event.stream = message.key.stream;
    event.id = message.key.id;
    event.ordered = !message.key.unordered;
    event.ppid = pieces.front().ppid;
    event.first_tsn = pieces.front().tsn;
    event.last_tsn = pieces.back().tsn;
    for (const auto& chunk : pieces) {
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
    while (chunks_by_tsn_.count(cumulative_tsn_ + 1) || abandoned_tsns_.count(cumulative_tsn_ + 1)) {
        ++cumulative_tsn_;
    }

    std::vector<MessageKey> ready;
    for (const auto& [key, message] : messages_) {
        if (message_complete(message) && message_committed(message)) {
            auto state = streams_[key.stream];
            uint32_t next = key.idata ? state.next_mid : state.next_ssn;
            if (key.unordered || key.id == next) {
                ready.push_back(key);
            }
        }
    }
    std::sort(ready.begin(), ready.end(), [&](const MessageKey& a, const MessageKey& b) {
        auto mina = messages_[a].chunks.front().tsn;
        auto minb = messages_[b].chunks.front().tsn;
        return mina < minb;
    });
    for (const auto& key : ready) {
        auto it = messages_.find(key);
        if (it == messages_.end()) continue;
        events_.push_back(make_delivery(it->second));
        if (!key.unordered) {
            if (key.idata) streams_[key.stream].next_mid++;
            else streams_[key.stream].next_ssn++;
        }
        messages_.erase(it);
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
    size_t buffered = chunks_by_tsn_.size();
    output << "  ],\n  \"state\": {\"cumulative_tsn\":" << cumulative_tsn_
           << ",\"buffered_chunks\":" << buffered
           << ",\"pending_messages\":" << messages_.size() << "}\n}\n";
}

}  // namespace sctp
