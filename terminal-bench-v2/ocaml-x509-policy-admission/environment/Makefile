OCAMLC ?= ocamlc

.PHONY: all clean

all: certadmit

certadmit: src/certadmit.ml
	$(OCAMLC) -o certadmit src/certadmit.ml

clean:
	rm -f certadmit src/*.cmi src/*.cmo
