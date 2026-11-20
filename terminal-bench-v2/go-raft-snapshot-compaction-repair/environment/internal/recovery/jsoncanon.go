package recovery

import (
	"bytes"
	"encoding/json"
	"sort"
	"strconv"
)

func canonicalJSON(v any) ([]byte, error) {
	var raw any
	b, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	if err := dec.Decode(&raw); err != nil {
		return nil, err
	}
	var out bytes.Buffer
	writeCanonical(&out, raw)
	return out.Bytes(), nil
}

func writeCanonical(out *bytes.Buffer, v any) {
	switch x := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				out.WriteByte(',')
			}
			kb, _ := json.Marshal(k)
			out.Write(kb)
			out.WriteByte(':')
			writeCanonical(out, x[k])
		}
		out.WriteByte('}')
	case []any:
		out.WriteByte('[')
		for i, item := range x {
			if i > 0 {
				out.WriteByte(',')
			}
			writeCanonical(out, item)
		}
		out.WriteByte(']')
	case string:
		b, _ := json.Marshal(x)
		out.Write(b)
	case json.Number:
		out.WriteString(x.String())
	case float64:
		out.WriteString(strconv.FormatFloat(x, 'f', -1, 64))
	case bool:
		if x {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case nil:
		out.WriteString("null")
	}
}
