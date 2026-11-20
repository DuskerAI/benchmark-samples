#pragma once

#include <cstdint>
#include <iosfwd>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace sctp {

struct Event {
    std::string type;
    std::string format;
    uint16_t stream = 0;
    uint32_t id = 0;
    bool ordered = true;
    uint32_t ppid = 0;
    uint32_t first_tsn = 0;
    uint32_t last_tsn = 0;
    std::string payload_hex;
    std::string reason;
    uint32_t next_ssn = 0;
    uint32_t next_mid = 0;
};

struct Chunk {
    bool idata = false;
    uint32_t tsn = 0;
    uint16_t stream = 0;
    uint32_t id = 0;
    uint32_t fsn = 0;
    bool begin = false;
    bool end = false;
    bool unordered = false;
    uint32_t ppid = 0;
    std::string payload_hex;
};

class Reassembler {
public:
    void process(std::istream& input);
    void write_json(std::ostream& output) const;

private:
    struct MessageKey {
        bool idata = false;
        uint16_t stream = 0;
        uint32_t id = 0;
        bool unordered = false;

        bool operator<(const MessageKey& other) const;
    };

    struct Message {
        MessageKey key;
        std::vector<Chunk> chunks;
        bool failed = false;
    };

    struct StreamState {
        uint32_t next_ssn = 0;
        uint32_t next_mid = 0;
    };

    uint32_t cumulative_tsn_ = 0;
    bool started_ = false;
    std::map<uint32_t, Chunk> chunks_by_tsn_;
    std::set<uint32_t> abandoned_tsns_;
    std::map<MessageKey, Message> messages_;
    std::map<uint16_t, StreamState> streams_;
    std::vector<Event> events_;

    void accept_chunk(const Chunk& chunk);
    void abandon(uint32_t first, uint32_t last);
    void reset(uint16_t stream, uint32_t next_ssn, uint32_t next_mid);
    void advance_and_deliver();
    bool message_complete(const Message& message) const;
    bool message_committed(const Message& message) const;
    Event make_delivery(const Message& message) const;
    void emit_skip(const Message& message);
};

}  // namespace sctp
