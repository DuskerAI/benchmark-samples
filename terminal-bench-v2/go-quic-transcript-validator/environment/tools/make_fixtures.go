package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"flag"
	"os"
	"path/filepath"
)

func main() {
	out := flag.String("out", "fixtures", "output directory")
	flag.Parse()
	_ = os.MkdirAll(*out, 0o755)
	odcid := hx("8394c8f03e515708")
	client := hx("a1a2a3a4a5a6a7a8")
	server := hx("b1b2b3b4b5b6b7b8")
	token := bytes.Repeat([]byte{0x51}, 70)
	var pkts [][]byte
	pkts = append(pkts, initial(odcid, client, nil, 0xff, 1, payload(1, []param{{0x40, []byte{1}}, {0, []byte{0, 0, 3, 0}}})))
	pkts = append(pkts, retry(odcid, client, server, token))
	pkts = append(pkts, initial(server, client, token, 0x00, 1, payload(1, []param{{0x39, bytes.Repeat([]byte{0x22}, 64)}})))
	pkts = append(pkts, initial(client, server, nil, 0x0000, 2, payload(2, []param{{7, []byte{0x11}}, {0x40, []byte{0x02, 0x03}}})))
	pkts = append(pkts, initial(server, client, token, 0x00, 1, payload(1, []param{{0x39, bytes.Repeat([]byte{0x22}, 64)}})))
	_ = os.WriteFile(filepath.Join(*out, "customer-alpha.qtr"), bundle(pkts), 0o644)
}

type param struct {
	id  uint64
	val []byte
}

func bundle(pkts [][]byte) []byte {
	b := []byte("QTV1")
	b = append(b, vint(uint64(len(pkts)))...)
	for _, p := range pkts {
		b = append(b, vint(uint64(len(p)))...)
		b = append(b, p...)
	}
	return b
}

func initial(dcid, scid, token []byte, pn uint64, pnLen int, pl []byte) []byte {
	first := byte(0xc0 | byte(pnLen-1))
	b := []byte{first}
	b = binary.BigEndian.AppendUint32(b, 0x00000001)
	b = append(b, byte(len(dcid)))
	b = append(b, dcid...)
	b = append(b, byte(len(scid)))
	b = append(b, scid...)
	b = append(b, vint(uint64(len(token)))...)
	b = append(b, token...)
	pnBytes := make([]byte, pnLen)
	for i := pnLen - 1; i >= 0; i-- {
		pnBytes[i] = byte(pn)
		pn >>= 8
	}
	b = append(b, vint(uint64(len(pnBytes)+len(pl)))...)
	b = append(b, pnBytes...)
	b = append(b, pl...)
	return b
}

func retry(odcid, dcid, scid, token []byte) []byte {
	b := []byte{0xf0}
	b = binary.BigEndian.AppendUint32(b, 0x00000001)
	b = append(b, byte(len(dcid)))
	b = append(b, dcid...)
	b = append(b, byte(len(scid)))
	b = append(b, scid...)
	b = append(b, vint(uint64(len(token)))...)
	b = append(b, token...)
	h := sha256.New()
	h.Write([]byte("retry-v1"))
	h.Write([]byte{byte(len(odcid))})
	h.Write(odcid)
	h.Write(b)
	b = append(b, h.Sum(nil)[:16]...)
	return b
}

func payload(endpoint byte, ps []param) []byte {
	b := []byte{endpoint}
	b = append(b, vint(uint64(len(ps)))...)
	for _, p := range ps {
		b = append(b, vint(p.id)...)
		b = append(b, vint(uint64(len(p.val)))...)
		b = append(b, p.val...)
	}
	return b
}

func vint(v uint64) []byte {
	if v < 64 {
		return []byte{byte(v)}
	}
	if v < 16384 {
		return []byte{byte(0x40 | (v >> 8)), byte(v)}
	}
	if v < 1073741824 {
		return []byte{byte(0x80 | (v >> 24)), byte(v >> 16), byte(v >> 8), byte(v)}
	}
	return []byte{byte(0xc0 | (v >> 56)), byte(v >> 48), byte(v >> 40), byte(v >> 32), byte(v >> 24), byte(v >> 16), byte(v >> 8), byte(v)}
}

func hx(s string) []byte {
	out := make([]byte, len(s)/2)
	for i := range out {
		var x byte
		for _, c := range []byte{s[2*i], s[2*i+1]} {
			x <<= 4
			if c <= '9' {
				x |= c - '0'
			} else {
				x |= c - 'a' + 10
			}
		}
		out[i] = x
	}
	return out
}
