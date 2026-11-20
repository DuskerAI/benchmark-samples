.PHONY: all clean

ERLC ?= erlc

all: bin/mqtt_replay

build:
	mkdir -p build bin

build/%.beam: src/%.erl | build
	$(ERLC) -Wall -o build $<

bin/mqtt_replay: build/frame_scan.beam build/json_emit.beam build/session_state.beam build/mqtt_replay.beam | build
	printf '%s\n' '#!/usr/bin/env escript' '%%! -pa /app/mqtt_replay/build' 'main(Args) ->' '    case mqtt_replay:main(Args) of' '        ok -> halt(0);' '        {error, Message} -> io:format(standard_error, "~s~n", [fmt(Message)]), halt(1)' '    end.' '' 'fmt(Message) when is_list(Message) -> Message;' 'fmt(Message) -> lists:flatten(io_lib:format("~p", [Message])).' > $@
	chmod +x $@

clean:
	rm -rf build bin
