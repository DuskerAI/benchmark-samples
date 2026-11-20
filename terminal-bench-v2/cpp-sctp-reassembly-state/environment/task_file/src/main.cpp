#include "reassembler.hpp"

#include <fstream>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: sctp_replay <input.trace> <output.json>\n";
        return 2;
    }

    try {
        std::ifstream input(argv[1]);
        if (!input) {
            throw std::runtime_error("could not open input");
        }
        sctp::Reassembler reassembler;
        reassembler.process(input);

        std::ofstream output(argv[2]);
        if (!output) {
            throw std::runtime_error("could not open output");
        }
        reassembler.write_json(output);
    } catch (const std::exception& exc) {
        std::cerr << "sctp_replay: " << exc.what() << "\n";
        return 1;
    }

    return 0;
}
