import json
import math
import subprocess
from pathlib import Path

APP = Path("/app")
MODEL = APP / "model" / "tinyq.json"


def deq4(section, row_id, dim):
    row = section["rows"][row_id]
    zero = section["zero"]
    scale = section["scale"]
    out = []
    for i in range(dim):
        packed = row[i // 2]
        raw = (packed >> (4 * (i % 2))) & 15
        out.append((raw - zero[i]) * scale[i])
    return out


def deq8(section, row_id):
    return [(v - section["zero"]) * section["scale"] for v in section["rows"][row_id]]


def sigmoid(value):
    return 1.0 / (1.0 + math.exp(-value))


def pack4(raw_values):
    return [raw_values[i] | (raw_values[i + 1] << 4) for i in range(0, len(raw_values), 2)]


def layered_model():
    return {
        "dim": 4,
        "decay": 0.37,
        "unk_id": 0,
        "tokens": [
            {"id": 4, "text": "tail"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "ab"},
            {"id": 1, "text": "a"},
            {"id": 3, "text": "b"},
            {"id": 5, "text": "µ"},
        ],
        "embed": {
            "zero": [8, 5, 9, 4],
            "scale": [0.07, 0.11, 0.05, 0.13],
            "rows": [
                pack4([8, 5, 9, 4]),
                pack4([10, 3, 12, 1]),
                pack4([2, 14, 5, 10]),
                pack4([15, 8, 4, 13]),
                pack4([6, 7, 11, 2]),
                pack4([12, 1, 15, 9]),
            ],
        },
        "pos": {
            "zero": -2,
            "scale": 0.09,
            "rows": [
                [0, -3, 4, 1],
                [5, 2, -1, 3],
                [-2, 6, 1, -4],
                [3, -5, 2, 5],
            ],
        },
        "out": {
            "zero": [7, 8, 6, 9],
            "scale": [0.06, 0.04, 0.08, 0.05],
            "rows": [
                pack4([7, 8, 6, 9]),
                pack4([11, 5, 8, 3]),
                pack4([4, 13, 10, 12]),
                pack4([14, 9, 2, 15]),
                pack4([3, 6, 12, 5]),
                pack4([10, 2, 14, 7]),
            ],
        },
        "bias": [0.0, 0.011, -0.009, 0.017, -0.013, 0.005],
    }


def ragged_model():
    return {
        "dim": 3,
        "decay": -0.28,
        "unk_id": 0,
        "tokens": [
            {"id": 5, "text": "xy"},
            {"id": 0, "text": "?"},
            {"id": 4, "text": "z"},
            {"id": 2, "text": "xy"},
            {"id": 1, "text": "x"},
            {"id": 3, "text": "y"},
        ],
        "embed": {
            "zero": [7, 6, 5],
            "scale": [0.12, 0.03, 0.08],
            "rows": [
                pack4([7, 6, 5, 0]),
                pack4([9, 3, 8, 0]),
                pack4([1, 14, 2, 0]),
                pack4([13, 5, 11, 0]),
                pack4([3, 8, 15, 0]),
                pack4([11, 2, 6, 0]),
            ],
        },
        "pos": {
            "zero": 1,
            "scale": 0.07,
            "rows": [
                [2, -3, 4],
                [-4, 5, 1],
                [3, 0, -5],
                [-1, 4, 2],
                [5, -2, -3],
            ],
        },
        "out": {
            "zero": [6, 7, 8],
            "scale": [0.05, 0.09, 0.04],
            "rows": [
                pack4([6, 7, 8, 0]),
                pack4([12, 4, 6, 0]),
                pack4([2, 15, 11, 0]),
                pack4([14, 8, 3, 0]),
                pack4([5, 1, 13, 0]),
                pack4([9, 12, 4, 0]),
            ],
        },
        "bias": [0.0, -0.027, 0.018, 0.011, -0.006, 0.024],
    }


def multilayer_model():
    return {
        "dim": 5,
        "unk_id": 0,
        "tokens": [
            {"id": 6, "text": "na"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "n"},
            {"id": 4, "text": "⚙"},
            {"id": 1, "text": ""},
            {"id": 3, "text": "a"},
            {"id": 5, "text": "µ"},
        ],
        "layers": [
            {
                "decay": 0.43,
                "embed": {
                    "zero": [8, 7, 6, 5, 4],
                    "scale": [0.05, 0.07, 0.04, 0.03, 0.06],
                    "rows": [
                        pack4([8, 7, 6, 5, 4, 0]),
                        pack4([9, 6, 8, 7, 3, 0]),
                        pack4([3, 12, 5, 8, 11, 0]),
                        pack4([11, 4, 13, 2, 9, 0]),
                        pack4([15, 8, 4, 10, 6, 0]),
                        pack4([2, 14, 7, 9, 12, 0]),
                        pack4([12, 5, 10, 6, 1, 0]),
                    ],
                },
                "pos": {
                    "zero": -1,
                    "scale": 0.08,
                    "rows": [[0, 2, -3, 4, 1], [5, -1, 2, -4, 3], [-2, 3, 5, 1, -5]],
                },
            },
            {
                "decay": -0.31,
                "embed": {
                    "zero": [7, 8, 7, 8, 7],
                    "scale": [0.04, 0.06, 0.05, 0.07, 0.03],
                    "rows": [
                        pack4([7, 8, 7, 8, 7, 0]),
                        pack4([11, 5, 9, 3, 12, 0]),
                        pack4([4, 13, 2, 14, 6, 0]),
                        pack4([15, 1, 12, 6, 10, 0]),
                        pack4([5, 10, 4, 15, 2, 0]),
                        pack4([13, 3, 15, 1, 9, 0]),
                        pack4([2, 12, 11, 5, 14, 0]),
                    ],
                },
                "pos": {
                    "zero": 2,
                    "scale": 0.05,
                    "rows": [[3, -2, 4, -1, 0], [-4, 5, 1, 2, -3], [2, 0, -5, 3, 4], [1, -4, 2, 5, -2]],
                },
            },
        ],
        "out": {
            "zero": [6, 7, 8, 6, 7],
            "scale": [0.05, 0.04, 0.06, 0.03, 0.07],
            "rows": [
                pack4([6, 7, 8, 6, 7, 0]),
                pack4([10, 4, 12, 3, 9, 0]),
                pack4([1, 15, 6, 12, 5, 0]),
                pack4([14, 8, 3, 15, 2, 0]),
                pack4([5, 2, 14, 7, 11, 0]),
                pack4([12, 11, 4, 9, 1, 0]),
                pack4([3, 13, 10, 2, 15, 0]),
            ],
        },
        "bias": [0.0, 0.0, 0.021, -0.017, 0.013, -0.009, 0.006],
    }


def projected_gated_model():
    return {
        "dim": 4,
        "unk_id": 0,
        "tokens": [
            {"id": 7, "text": "tone"},
            {"id": 0, "text": "?"},
            {"id": 4, "text": "to"},
            {"id": 2, "text": "t"},
            {"id": 5, "text": "ne"},
            {"id": 1, "text": "o"},
            {"id": 3, "text": "n"},
            {"id": 6, "text": "µ"},
        ],
        "layers": [
            {
                "decay": 0.52,
                "embed": {
                    "zero": [7, 8, 6, 9],
                    "scale": [0.05, 0.04, 0.06, 0.03],
                    "rows": [
                        pack4([7, 8, 6, 9]),
                        pack4([9, 5, 11, 4]),
                        pack4([3, 14, 5, 12]),
                        pack4([12, 3, 13, 6]),
                        pack4([5, 10, 4, 15]),
                        pack4([14, 6, 9, 2]),
                        pack4([2, 15, 7, 11]),
                        pack4([11, 4, 12, 5]),
                    ],
                },
                "pos": {"zero": -2, "scale": 0.07, "rows": [[0, 3, -2, 4], [5, -1, 2, -4], [-3, 4, 1, 0]]},
                "gate": {"zero": 0, "scale": 0.2, "rows": [[-2, 1, 0, 3], [2, -1, 4, 0], [0, 3, -3, 2], [4, 0, 1, -2], [-1, 5, 2, 0], [3, -4, 0, 1], [1, 2, -5, 3], [5, -2, 1, 0]]},
            },
            {
                "decay": -0.24,
                "embed": {
                    "zero": [8, 7, 8, 7],
                    "scale": [0.04, 0.06, 0.05, 0.07],
                    "rows": [
                        pack4([8, 7, 8, 7]),
                        pack4([12, 4, 10, 2]),
                        pack4([1, 15, 6, 13]),
                        pack4([14, 3, 12, 5]),
                        pack4([6, 11, 4, 15]),
                        pack4([15, 2, 13, 1]),
                        pack4([3, 14, 5, 11]),
                        pack4([10, 5, 15, 4]),
                    ],
                },
                "pos": {"zero": 1, "scale": 0.05, "rows": [[3, -2, 4, 0], [-4, 5, 1, -3], [2, 0, -5, 3], [1, -4, 2, 5]]},
                "carry_proj": {
                    "zero": [7, 8, 7, 8],
                    "scale": [0.08, 0.05, 0.06, 0.04],
                    "rows": [pack4([10, 6, 8, 5]), pack4([4, 12, 9, 7]), pack4([8, 5, 13, 6]), pack4([11, 9, 3, 14])],
                },
                "gate": {"zero": -1, "scale": 0.17, "rows": [[0, 2, -1, 3], [4, -2, 1, 0], [-3, 5, 2, -1], [1, 0, 4, -4], [2, -1, 3, 1], [-2, 6, 0, 2], [5, 1, -3, 0], [3, -4, 2, 5]]},
            },
        ],
        "norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.125, "rows": [[8, 5, 11, 6]]}},
        "logit_scale": 0.73,
        "out": {
            "zero": [6, 7, 8, 6],
            "scale": [0.045, 0.035, 0.055, 0.065],
            "rows": [
                pack4([6, 7, 8, 6]),
                pack4([11, 5, 13, 2]),
                pack4([2, 15, 5, 12]),
                pack4([14, 4, 11, 3]),
                pack4([5, 12, 3, 15]),
                pack4([13, 1, 14, 4]),
                pack4([3, 13, 6, 10]),
                pack4([10, 6, 15, 1]),
            ],
        },
        "bias": [0.0, -0.018, 0.011, 0.004, -0.009, 0.016, -0.013, 0.007],
    }


def mixed_clipped_model():
    return {
        "dim": 4,
        "unk_id": 0,
        "tokens": [
            {"id": 5, "text": "red"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "r"},
            {"id": 4, "text": "ed"},
            {"id": 1, "text": "e"},
            {"id": 3, "text": "d"},
        ],
        "layers": [
            {
                "decay": 0.67,
                "embed": {
                    "zero": [8, 8, 7, 7],
                    "scale": [0.09, 0.04, 0.06, 0.05],
                    "rows": [
                        pack4([8, 8, 7, 7]),
                        pack4([10, 5, 9, 4]),
                        pack4([3, 14, 5, 12]),
                        pack4([14, 4, 13, 3]),
                        pack4([6, 11, 2, 15]),
                        pack4([13, 2, 12, 5]),
                    ],
                },
                "pos": {"zero": -1, "scale": 0.08, "rows": [[0, 4, -2, 3], [5, -1, 2, -4], [-3, 1, 5, 0]]},
                "input_mix": {
                    "zero": [8, 8, 8, 8],
                    "scale": [0.18, 0.12, 0.15, 0.11],
                    "rows": [pack4([13, 6, 9, 4]), pack4([5, 14, 3, 10]), pack4([9, 2, 15, 5]), pack4([4, 11, 6, 14])],
                },
                "state_clip": {"limit": 0.42},
            },
            {
                "decay": -0.38,
                "embed": {
                    "zero": [7, 8, 7, 8],
                    "scale": [0.06, 0.05, 0.04, 0.07],
                    "rows": [
                        pack4([7, 8, 7, 8]),
                        pack4([11, 5, 10, 3]),
                        pack4([2, 15, 6, 13]),
                        pack4([14, 4, 12, 6]),
                        pack4([6, 12, 3, 15]),
                        pack4([15, 1, 14, 4]),
                    ],
                },
                "pos": {"zero": 2, "scale": 0.06, "rows": [[2, -3, 4, 0], [-5, 4, 1, -2], [1, 0, -4, 5], [3, -1, 2, -5]]},
                "carry_proj": {
                    "zero": [8, 7, 8, 7],
                    "scale": [0.07, 0.05, 0.08, 0.06],
                    "rows": [pack4([12, 5, 9, 6]), pack4([3, 13, 8, 4]), pack4([10, 6, 14, 5]), pack4([5, 11, 2, 15])],
                },
                "gate": {"zero": 0, "scale": 0.19, "rows": [[-1, 2, 0, 3], [4, -2, 1, 0], [-3, 5, 2, -1], [1, 0, 4, -4], [2, -1, 3, 1], [-2, 6, 0, 2]]},
                "input_mix": {
                    "zero": [7, 8, 7, 8],
                    "scale": [0.1, 0.09, 0.08, 0.07],
                    "rows": [pack4([10, 6, 8, 5]), pack4([5, 12, 4, 11]), pack4([9, 3, 13, 6]), pack4([4, 10, 5, 14])],
                },
                "state_clip": {"limit": 0.35},
            },
        ],
        "norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.125, "rows": [[9, 6, 10, 5]]}},
        "logit_scale": 1.17,
        "out": {
            "zero": [6, 7, 8, 6],
            "scale": [0.055, 0.045, 0.065, 0.035],
            "rows": [pack4([6, 7, 8, 6]), pack4([10, 4, 12, 3]), pack4([2, 15, 5, 13]), pack4([14, 8, 3, 15]), pack4([5, 2, 14, 7]), pack4([13, 11, 4, 9])],
        },
        "bias": [0.0, 0.019, -0.014, 0.007, -0.006, 0.012],
    }


def feedback_head_model():
    return {
        "dim": 4,
        "unk_id": 0,
        "tokens": [
            {"id": 5, "text": "flux"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "fl"},
            {"id": 1, "text": "f"},
            {"id": 3, "text": "ux"},
            {"id": 4, "text": "µ"},
        ],
        "layers": [
            {
                "decay": 0.58,
                "embed": {
                    "zero": [8, 7, 8, 7],
                    "scale": [0.06, 0.04, 0.05, 0.07],
                    "rows": [pack4([8, 7, 8, 7]), pack4([10, 5, 9, 4]), pack4([3, 14, 5, 12]), pack4([14, 4, 13, 3]), pack4([6, 11, 2, 15]), pack4([13, 2, 12, 5])],
                },
                "pos": {"zero": -2, "scale": 0.07, "rows": [[0, 3, -2, 4], [5, -1, 2, -4], [-3, 4, 1, 0]]},
                "feedback_mix": {
                    "zero": [8, 8, 8, 8],
                    "scale": [0.05, 0.07, 0.06, 0.04],
                    "rows": [pack4([10, 6, 9, 7]), pack4([5, 12, 4, 11]), pack4([9, 3, 13, 6]), pack4([4, 10, 5, 14])],
                },
                "state_clip": {"limit": 0.55},
            },
            {
                "decay": -0.27,
                "embed": {
                    "zero": [7, 8, 7, 8],
                    "scale": [0.05, 0.06, 0.04, 0.08],
                    "rows": [pack4([7, 8, 7, 8]), pack4([11, 5, 10, 3]), pack4([2, 15, 6, 13]), pack4([14, 4, 12, 6]), pack4([6, 12, 3, 15]), pack4([15, 1, 14, 4])],
                },
                "pos": {"zero": 1, "scale": 0.06, "rows": [[2, -3, 4, 0], [-5, 4, 1, -2], [1, 0, -4, 5], [3, -1, 2, -5]]},
                "carry_proj": {"zero": [8, 7, 8, 7], "scale": [0.07, 0.05, 0.08, 0.06], "rows": [pack4([12, 5, 9, 6]), pack4([3, 13, 8, 4]), pack4([10, 6, 14, 5]), pack4([5, 11, 2, 15])]},
                "input_mix": {"zero": [7, 8, 7, 8], "scale": [0.09, 0.08, 0.07, 0.06], "rows": [pack4([10, 6, 8, 5]), pack4([5, 12, 4, 11]), pack4([9, 3, 13, 6]), pack4([4, 10, 5, 14])]},
                "gate": {"zero": 0, "scale": 0.16, "rows": [[-1, 2, 0, 3], [4, -2, 1, 0], [-3, 5, 2, -1], [1, 0, 4, -4], [2, -1, 3, 1], [-2, 6, 0, 2]]},
                "feedback_mix": {"zero": [8, 8, 8, 8], "scale": [0.04, 0.05, 0.06, 0.07], "rows": [pack4([11, 5, 8, 6]), pack4([4, 13, 9, 7]), pack4([8, 6, 14, 5]), pack4([5, 12, 3, 15])]},
                "state_clip": {"limit": 0.38},
            },
        ],
        "norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.125, "rows": [[9, 6, 10, 5]]}},
        "head_mix": {"zero": [8, 8, 8, 8], "scale": [0.08, 0.05, 0.07, 0.06], "rows": [pack4([12, 5, 9, 6]), pack4([4, 13, 8, 7]), pack4([9, 6, 14, 5]), pack4([5, 11, 3, 15])]},
        "position_bias": {"zero": 0, "scale": 0.013, "rows": [[0, 2, -1, 3, -2, 1], [1, -3, 2, 0, 4, -2], [-2, 1, 0, -4, 3, 2]]},
        "logit_scale": 0.91,
        "out": {"zero": [6, 7, 8, 6], "scale": [0.055, 0.045, 0.065, 0.035], "rows": [pack4([6, 7, 8, 6]), pack4([10, 4, 12, 3]), pack4([2, 15, 5, 13]), pack4([14, 8, 3, 15]), pack4([5, 2, 14, 7]), pack4([13, 11, 4, 9])]},
        "bias": [0.0, 0.019, -0.014, 0.007, -0.006, 0.012],
    }


def normed_carry_model():
    return {
        "dim": 5,
        "unk_id": 0,
        "tokens": [
            {"id": 6, "text": "ra"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "r"},
            {"id": 5, "text": "ay"},
            {"id": 1, "text": "a"},
            {"id": 3, "text": "y"},
            {"id": 4, "text": "µ"},
        ],
        "layers": [
            {
                "decay": 0.49,
                "state_init": {"zero": 0, "scale": 0.035, "rows": [[4, -3, 2, 5, -1]]},
                "embed": {"zero": [8, 7, 8, 7, 8], "scale": [0.05, 0.06, 0.04, 0.07, 0.03], "rows": [pack4([8, 7, 8, 7, 8, 0]), pack4([11, 5, 10, 3, 9, 0]), pack4([2, 15, 6, 13, 5, 0]), pack4([14, 4, 12, 6, 11, 0]), pack4([6, 12, 3, 15, 2, 0]), pack4([15, 1, 14, 4, 12, 0]), pack4([3, 13, 5, 12, 6, 0])]},
                "pos": {"zero": -1, "scale": 0.07, "rows": [[0, 3, -2, 4, 1], [5, -1, 2, -4, 3], [-3, 4, 1, 0, -5]]},
                "feedback_mix": {"zero": [8, 8, 8, 8, 8], "scale": [0.04, 0.05, 0.06, 0.07, 0.03], "rows": [pack4([11, 5, 8, 6, 12, 0]), pack4([4, 13, 9, 7, 3, 0]), pack4([8, 6, 14, 5, 10, 0]), pack4([5, 12, 3, 15, 7, 0]), pack4([9, 4, 12, 6, 14, 0])]},
                "state_clip": {"limit": 0.52},
                "state_norm": {"eps": 1e-5, "gain": {"zero": 0, "scale": 0.125, "rows": [[9, 6, 11, 5, 8]]}},
            },
            {
                "decay": -0.36,
                "embed": {"zero": [7, 8, 7, 8, 7], "scale": [0.04, 0.05, 0.07, 0.06, 0.08], "rows": [pack4([7, 8, 7, 8, 7, 0]), pack4([10, 4, 12, 3, 9, 0]), pack4([1, 15, 6, 12, 5, 0]), pack4([14, 8, 3, 15, 2, 0]), pack4([5, 2, 14, 7, 11, 0]), pack4([12, 11, 4, 9, 1, 0]), pack4([3, 13, 10, 2, 15, 0])]},
                "pos": {"zero": 2, "scale": 0.05, "rows": [[3, -2, 4, -1, 0], [-4, 5, 1, 2, -3], [2, 0, -5, 3, 4], [1, -4, 2, 5, -2]]},
                "carry_proj": {"zero": [7, 8, 7, 8, 7], "scale": [0.06, 0.05, 0.08, 0.04, 0.07], "rows": [pack4([10, 6, 8, 5, 12, 0]), pack4([4, 12, 9, 7, 3, 0]), pack4([8, 5, 13, 6, 11, 0]), pack4([11, 9, 3, 14, 5, 0]), pack4([6, 10, 4, 12, 15, 0])]},
                "gate": {"zero": 0, "scale": 0.14, "rows": [[-1, 2, 0, 3, 1], [4, -2, 1, 0, -3], [-3, 5, 2, -1, 4], [1, 0, 4, -4, 2], [2, -1, 3, 1, -2], [-2, 6, 0, 2, 5], [5, 1, -3, 0, 2]]},
                "residual_carry": 0.31,
                "state_clip": {"limit": 0.44},
                "state_norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.1, "rows": [[8, 11, 6, 10, 7]]}},
            },
        ],
        "readout_layers": [{"layer": 0, "weight": -0.25}, {"layer": 1, "weight": 1.2}],
        "norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.125, "rows": [[8, 7, 10, 5, 9]]}},
        "head_mix": {"zero": [8, 8, 8, 8, 8], "scale": [0.06, 0.05, 0.07, 0.08, 0.04], "rows": [pack4([12, 5, 9, 6, 10, 0]), pack4([4, 13, 8, 7, 3, 0]), pack4([9, 6, 14, 5, 11, 0]), pack4([5, 11, 3, 15, 7, 0]), pack4([8, 4, 12, 6, 14, 0])]},
        "position_bias": {"zero": 0, "scale": 0.012, "rows": [[0, 2, -1, 3, -2, 1, 4], [1, -3, 2, 0, 4, -2, -1], [-2, 1, 0, -4, 3, 2, 5]]},
        "logit_scale": 0.88,
        "out": {"zero": [6, 7, 8, 6, 7], "scale": [0.05, 0.04, 0.06, 0.03, 0.07], "rows": [pack4([6, 7, 8, 6, 7, 0]), pack4([10, 4, 12, 3, 9, 0]), pack4([1, 15, 6, 12, 5, 0]), pack4([14, 8, 3, 15, 2, 0]), pack4([5, 2, 14, 7, 11, 0]), pack4([12, 11, 4, 9, 1, 0]), pack4([3, 13, 10, 2, 15, 0])]},
        "bias": [0.0, 0.017, -0.012, 0.006, -0.009, 0.014, -0.016],
    }


def delayed_history_model():
    model = normed_carry_model()
    model["layers"][0]["history_mix"] = {
        "lag": 3,
        "mix": {
            "zero": [8, 8, 8, 8, 8],
            "scale": [0.035, 0.05, 0.045, 0.04, 0.055],
            "rows": [
                pack4([11, 5, 8, 6, 12, 0]),
                pack4([4, 13, 9, 7, 3, 0]),
                pack4([8, 6, 14, 5, 10, 0]),
                pack4([5, 12, 3, 15, 7, 0]),
                pack4([9, 4, 12, 6, 14, 0]),
            ],
        },
    }
    model["layers"][1]["history_mix"] = {
        "lag": 1,
        "mix": {
            "zero": [7, 8, 7, 8, 7],
            "scale": [0.04, 0.035, 0.05, 0.045, 0.03],
            "rows": [
                pack4([10, 6, 8, 5, 12, 0]),
                pack4([4, 12, 9, 7, 3, 0]),
                pack4([8, 5, 13, 6, 11, 0]),
                pack4([11, 9, 3, 14, 5, 0]),
                pack4([6, 10, 4, 12, 15, 0]),
            ],
        },
    }
    model["group_bias"] = {
        "groups": {"0": 0, "1": 1, "2": 1, "3": 2, "4": 0, "5": 2, "6": 3},
        "scale": 0.019,
        "rows": [
            [0, 2, -1, 3, -2, 1, 4],
            [1, -3, 2, 0, 4, -2, -1],
            [-2, 1, 0, -4, 3, 2, 5],
            [3, 0, -2, 1, -1, 4, -3],
        ],
    }
    model["transition_bias"] = {
        "scale": 0.014,
        "rows": [
            [0, 1, -2, 3, -1, 2, 4],
            [2, -1, 3, 0, 1, -3, 2],
            [-1, 4, 0, -2, 3, 1, -4],
            [3, 0, -1, 2, -3, 4, 1],
            [1, -2, 4, -1, 0, 3, -2],
            [-3, 2, 1, 4, -2, 0, 3],
            [4, 1, -3, 0, 2, -1, 5],
        ],
    }
    return model



def initialized_readout_model():
    return {
        "dim": 4,
        "unk_id": 0,
        "tokens": [
            {"id": 6, "text": "ion"},
            {"id": 0, "text": "?"},
            {"id": 2, "text": "i"},
            {"id": 5, "text": "on"},
            {"id": 1, "text": "o"},
            {"id": 3, "text": "n"},
            {"id": 4, "text": "µ"},
        ],
        "layers": [
            {
                "decay": 0.61,
                "state_init": {"zero": 0, "scale": 0.03, "rows": [[3, -2, 4, 1]]},
                "embed": {
                    "zero": [8, 7, 8, 7],
                    "scale": [0.06, 0.04, 0.05, 0.07],
                    "rows": [pack4([8, 7, 8, 7]), pack4([10, 5, 9, 4]), pack4([3, 14, 5, 12]), pack4([14, 4, 13, 3]), pack4([6, 11, 2, 15]), pack4([13, 2, 12, 5]), pack4([4, 15, 6, 10])],
                },
                "pos": {"zero": -2, "scale": 0.07, "rows": [[0, 3, -2, 4], [5, -1, 2, -4], [-3, 4, 1, 0]]},
                "input_mix": {"zero": [8, 8, 8, 8], "scale": [0.11, 0.08, 0.09, 0.07], "rows": [pack4([12, 5, 9, 7]), pack4([4, 13, 8, 6]), pack4([9, 6, 14, 5]), pack4([5, 11, 3, 15])]},
                "state_clip": {"limit": 0.46},
            },
            {
                "decay": -0.33,
                "state_init": {"zero": 0, "scale": 0.025, "rows": [[-1, 5, -3, 2]]},
                "embed": {
                    "zero": [7, 8, 7, 8],
                    "scale": [0.05, 0.06, 0.04, 0.08],
                    "rows": [pack4([7, 8, 7, 8]), pack4([11, 5, 10, 3]), pack4([2, 15, 6, 13]), pack4([14, 4, 12, 6]), pack4([6, 12, 3, 15]), pack4([15, 1, 14, 4]), pack4([3, 13, 5, 12])],
                },
                "pos": {"zero": 1, "scale": 0.06, "rows": [[2, -3, 4, 0], [-5, 4, 1, -2], [1, 0, -4, 5], [3, -1, 2, -5]]},
                "carry_proj": {"zero": [8, 7, 8, 7], "scale": [0.07, 0.05, 0.08, 0.06], "rows": [pack4([12, 5, 9, 6]), pack4([3, 13, 8, 4]), pack4([10, 6, 14, 5]), pack4([5, 11, 2, 15])]},
                "gate": {"zero": 0, "scale": 0.16, "rows": [[-1, 2, 0, 3], [4, -2, 1, 0], [-3, 5, 2, -1], [1, 0, 4, -4], [2, -1, 3, 1], [-2, 6, 0, 2], [5, 1, -3, 0]]},
                "feedback_mix": {"zero": [8, 8, 8, 8], "scale": [0.04, 0.05, 0.06, 0.07], "rows": [pack4([11, 5, 8, 6]), pack4([4, 13, 9, 7]), pack4([8, 6, 14, 5]), pack4([5, 12, 3, 15])]},
                "residual_carry": 0.37,
                "state_clip": {"limit": 0.41},
            },
            {
                "decay": 0.22,
                "embed": {
                    "zero": [8, 8, 7, 7],
                    "scale": [0.04, 0.07, 0.06, 0.05],
                    "rows": [pack4([8, 8, 7, 7]), pack4([10, 5, 9, 4]), pack4([3, 14, 5, 12]), pack4([14, 4, 13, 3]), pack4([6, 11, 2, 15]), pack4([13, 2, 12, 5]), pack4([4, 15, 6, 10])],
                },
                "pos": {"zero": -1, "scale": 0.05, "rows": [[0, 4, -2, 3], [5, -1, 2, -4], [-3, 1, 5, 0]]},
                "carry_proj": {"zero": [7, 8, 7, 8], "scale": [0.08, 0.04, 0.07, 0.05], "rows": [pack4([11, 6, 9, 5]), pack4([4, 12, 8, 7]), pack4([9, 5, 14, 6]), pack4([5, 10, 3, 15])]},
                "residual_carry": -0.28,
                "state_clip": {"limit": 0.39},
            },
        ],
        "readout_layers": [{"layer": 0, "weight": 0.2}, {"layer": 1, "weight": -0.35}, {"layer": 2, "weight": 1.1}],
        "norm": {"eps": 1e-6, "gain": {"zero": 0, "scale": 0.125, "rows": [[8, 7, 10, 5]]}},
        "head_mix": {"zero": [8, 8, 8, 8], "scale": [0.07, 0.06, 0.08, 0.05], "rows": [pack4([12, 5, 9, 6]), pack4([4, 13, 8, 7]), pack4([9, 6, 14, 5]), pack4([5, 11, 3, 15])]},
        "position_bias": {"zero": 0, "scale": 0.011, "rows": [[0, 2, -1, 3, -2, 1, 4], [1, -3, 2, 0, 4, -2, -1], [-2, 1, 0, -4, 3, 2, 5]]},
        "logit_scale": 0.84,
        "out": {"zero": [6, 7, 8, 6], "scale": [0.055, 0.045, 0.065, 0.035], "rows": [pack4([6, 7, 8, 6]), pack4([10, 4, 12, 3]), pack4([2, 15, 5, 13]), pack4([14, 8, 3, 15]), pack4([5, 2, 14, 7]), pack4([13, 11, 4, 9]), pack4([3, 13, 10, 2])]},
        "bias": [0.0, 0.019, -0.014, 0.007, -0.006, 0.012, -0.017],
    }

def tokenize(model, text):
    data = text.encode("utf-8")
    vocab = [(t["id"], t["text"].encode("utf-8")) for t in model["tokens"]]
    ids = []
    i = 0
    while i < len(data):
        best = None
        for token_id, raw in vocab:
            matched = raw and data.startswith(raw, i)
            better = best is None or len(raw) > len(best[1]) or (len(raw) == len(best[1]) and token_id < best[0])
            if matched and better:
                best = (token_id, raw)
        if best is None:
            ids.append(model["unk_id"])
            i += 1
        else:
            ids.append(best[0])
            i += len(best[1])
    return ids


def reference(model, prompt, max_tokens, chunks=None):
    dim = model["dim"]
    if "layers" in model:
        layers = model["layers"]
    else:
        layers = [{"decay": model["decay"], "embed": model["embed"], "pos": model["pos"]}]
    states = []
    for layer in layers:
        if "state_init" in layer:
            states.append(deq8(layer["state_init"], 0))
        else:
            states.append([0.0] * dim)
    histories = [[state[:]] for state in states]
    position = 0
    token_counts = {token["id"]: 0 for token in model["tokens"]}
    last_token_id = None

    def feed(token_id):
        nonlocal states, position, last_token_id
        carry = [0.0] * dim
        for layer_index, layer in enumerate(layers):
            prior_state = states[layer_index][:]
            emb = deq4(layer["embed"], token_id, dim)
            pos = deq8(layer["pos"], position % len(layer["pos"]["rows"]))
            layer_carry = carry
            if "carry_proj" in layer:
                layer_carry = [
                    sum(deq4(layer["carry_proj"], row_index, dim)[j] * carry[j] for j in range(dim))
                    for row_index in range(dim)
                ]
            layer_input = [emb[i] + pos[i] + layer_carry[i] for i in range(dim)]
            if "input_mix" in layer:
                layer_input = [
                    sum(deq4(layer["input_mix"], row_index, dim)[j] * layer_input[j] for j in range(dim))
                    for row_index in range(dim)
                ]
            if "token_shift" in layer:
                shift = deq8(layer["token_shift"], token_id)
                layer_input = [layer_input[i] + shift[i] for i in range(dim)]
            gate = [1.0] * dim
            if "gate" in layer:
                gate = [sigmoid(v) for v in deq8(layer["gate"], token_id)]
            states[layer_index] = [
                states[layer_index][i] * layer["decay"] + layer_input[i] * gate[i]
                for i in range(dim)
            ]
            if "state_clip" in layer:
                limit = layer["state_clip"]["limit"]
                states[layer_index] = [max(-limit, min(limit, value)) for value in states[layer_index]]
            if "feedback_mix" in layer:
                states[layer_index] = [
                    states[layer_index][row_index]
                    + sum(deq4(layer["feedback_mix"], row_index, dim)[j] * prior_state[j] for j in range(dim))
                    for row_index in range(dim)
                ]
                if "state_clip" in layer:
                    limit = layer["state_clip"]["limit"]
                    states[layer_index] = [max(-limit, min(limit, value)) for value in states[layer_index]]
            if "residual_carry" in layer:
                weight = layer["residual_carry"]
                states[layer_index] = [states[layer_index][i] + weight * layer_carry[i] for i in range(dim)]
                if "state_clip" in layer:
                    limit = layer["state_clip"]["limit"]
                    states[layer_index] = [max(-limit, min(limit, value)) for value in states[layer_index]]
            if "history_mix" in layer:
                lag = layer["history_mix"]["lag"]
                history_index = max(0, len(histories[layer_index]) - lag - 1)
                selected = histories[layer_index][history_index]
                states[layer_index] = [
                    states[layer_index][row_index]
                    + sum(deq4(layer["history_mix"]["mix"], row_index, dim)[j] * selected[j] for j in range(dim))
                    for row_index in range(dim)
                ]
                if "state_clip" in layer:
                    limit = layer["state_clip"]["limit"]
                    states[layer_index] = [max(-limit, min(limit, value)) for value in states[layer_index]]
            if "state_norm" in layer:
                denom = math.sqrt(sum(v * v for v in states[layer_index]) / dim + layer["state_norm"]["eps"])
                gain = deq8(layer["state_norm"]["gain"], 0)
                states[layer_index] = [states[layer_index][i] / denom * gain[i] for i in range(dim)]
            carry = states[layer_index]
        for layer_index, state in enumerate(states):
            histories[layer_index].append(state[:])
        position += 1
        token_counts[token_id] = token_counts.get(token_id, 0) + 1
        last_token_id = token_id

    prompt_text = "".join(chunks) if chunks is not None else prompt
    for token_id in tokenize(model, prompt_text):
        feed(token_id)

    generated = []
    logits = []
    for _ in range(max_tokens):
        logits = []
        if "readout_layers" in model:
            score_state = [0.0] * dim
            for entry in model["readout_layers"]:
                layer_state = states[entry["layer"]]
                weight = entry["weight"]
                score_state = [score_state[i] + weight * layer_state[i] for i in range(dim)]
        else:
            score_state = states[-1]
        if "norm" in model:
            denom = math.sqrt(sum(v * v for v in score_state) / dim + model["norm"]["eps"])
            gain = deq8(model["norm"]["gain"], 0)
            score_state = [score_state[i] / denom * gain[i] for i in range(dim)]
        if "head_mix" in model:
            score_state = [
                sum(deq4(model["head_mix"], row_index, dim)[j] * score_state[j] for j in range(dim))
                for row_index in range(dim)
            ]
        logit_scale = model.get("logit_scale", 1.0)
        position_bias = None
        if "position_bias" in model:
            position_bias = deq8(model["position_bias"], position % len(model["position_bias"]["rows"]))
        history_bias = None
        if "history_bias" in model and last_token_id is not None:
            history_bias = deq8(model["history_bias"], last_token_id)
        calibration = None
        if "calibration" in model:
            rows = model["calibration"]["rows"]
            scale = model["calibration"]["scale"]
            calibration = [value * scale for value in rows[position % len(rows)]]
        for tok in model["tokens"]:
            row = deq4(model["out"], tok["id"], dim)
            extra = position_bias[tok["id"]] if position_bias is not None else 0.0
            extra += history_bias[tok["id"]] if history_bias is not None else 0.0
            extra -= model.get("repeat_penalty", 0.0) * token_counts.get(tok["id"], 0)
            extra += calibration[tok["id"]] if calibration is not None else 0.0
            if "group_bias" in model and last_token_id is not None:
                groups = model["group_bias"]["groups"]
                last_group = groups.get(str(last_token_id))
                if last_group is not None:
                    rows = model["group_bias"]["rows"]
                    group_row = rows[(last_group + position) % len(rows)]
                    extra += group_row[tok["id"]] * model["group_bias"]["scale"]
            if "transition_bias" in model and last_token_id is not None:
                rows = model["transition_bias"]["rows"]
                if last_token_id < len(rows):
                    extra += rows[last_token_id][tok["id"]] * model["transition_bias"]["scale"]
            logits.append(sum(row[i] * score_state[i] for i in range(dim)) * logit_scale + model["bias"][tok["id"]] + extra)
        best_index = min(range(len(logits)), key=lambda i: (-logits[i], model["tokens"][i]["id"]))
        next_id = model["tokens"][best_index]["id"]
        generated.append(next_id)
        feed(next_id)
    texts = {t["id"]: t["text"] for t in model["tokens"]}
    return {"tokens": generated, "text": "".join(texts[i] for i in generated), "logits": logits}


def run_qinfer(model_path, prompt, max_tokens, chunks=None):
    cmd = ["qinfer", "--model", str(model_path), "--prompt", prompt, "--max-tokens", str(max_tokens)]
    if chunks is not None:
        cmd += ["--chunks", "|".join(chunks)]
    proc = subprocess.run(cmd, cwd=APP, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def assert_close(actual, expected):
    assert actual["tokens"] == expected["tokens"]
    assert actual["text"] == expected["text"]
    assert len(actual["logits"]) == len(expected["logits"])
    for a, e in zip(actual["logits"], expected["logits"]):
        assert math.isclose(a, e, rel_tol=0, abs_tol=1e-9)


def test_offline_scores_match_reference():
    """The bundled model matches the reference calculation, including calibration."""
    model = json.loads(MODEL.read_text())
    prompt = "crystal quant."
    expected = reference(model, prompt, 5)
    actual = run_qinfer(MODEL, prompt, 5)
    assert_close(actual, expected)


def test_non_ascii_prompt_matches_reference():
    """A mixed prompt produces the same result as the independent calculation."""
    model = json.loads(MODEL.read_text())
    prompt = "crystal⚑ quant"
    expected = reference(model, prompt, 4)
    actual = run_qinfer(MODEL, prompt, 4)
    assert_close(actual, expected)


def test_segmented_prompt_is_same_run():
    """A segmented prompt has the same generated result as the combined prompt."""
    model = json.loads(MODEL.read_text())
    chunks = ["cr", "ystal", " ", "quant", "."]
    expected = reference(model, "".join(chunks), 6, chunks=chunks)
    actual = run_qinfer(MODEL, "".join(chunks), 6, chunks=chunks)
    assert_close(actual, expected)
    full = run_qinfer(MODEL, "".join(chunks), 6)
    assert actual == full


def test_equal_scores_are_reproducible(tmp_path):
    """A model with equal scores returns the documented deterministic result."""
    tie_model = {
        "dim": 2,
        "decay": 0.5,
        "unk_id": 0,
        "tokens": [{"id": 0, "text": "a"}, {"id": 1, "text": "b"}, {"id": 2, "text": "c"}],
        "embed": {"zero": [0, 0], "scale": [1.0, 1.0], "rows": [[0], [0], [0]]},
        "pos": {"zero": 0, "scale": 1.0, "rows": [[0, 0]]},
        "out": {"zero": [0, 0], "scale": [1.0, 1.0], "rows": [[0], [0], [0]]},
        "bias": [0.0, 0.0, 0.0],
    }
    path = tmp_path / "tie.json"
    path.write_text(json.dumps(tie_model))
    expected = reference(tie_model, "a", 3)
    actual = run_qinfer(path, "a", 3)
    assert_close(actual, expected)


def test_layered_model_keeps_chunk_and_generation_state(tmp_path):
    """A custom model exercises chunk boundaries, token-list ordering, and reused generation state."""
    model = layered_model()
    path = tmp_path / "layered.json"
    path.write_text(json.dumps(model))
    chunks = ["a", "b", "µ", "ta", "il", "x"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 7, chunks=chunks)
    actual = run_qinfer(path, prompt, 7, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 7)


def test_ragged_quantization_and_byte_token_ties(tmp_path):
    """A custom odd-width model preserves byte-level token choice and packed columns."""
    model = ragged_model()
    path = tmp_path / "ragged.json"
    path.write_text(json.dumps(model))
    chunks = ["x", "y", "zz", "!"]
    prompt = "".join(chunks)
    assert tokenize(model, prompt) == [2, 4, 4, 0]
    expected = reference(model, prompt, 6, chunks=chunks)
    actual = run_qinfer(path, prompt, 6, chunks=chunks)
    assert_close(actual, expected)


def test_position_counter_wraps_without_resetting(tmp_path):
    """Long prompt and generated steps keep a single position stream across wraps."""
    model = ragged_model()
    path = tmp_path / "wraps.json"
    path.write_text(json.dumps(model))
    prompt = "xyzz!xyzz!xy"
    expected = reference(model, prompt, 9)
    actual = run_qinfer(path, prompt, 9)
    assert_close(actual, expected)


def test_multilayer_recurrence_uses_carry_and_shared_position(tmp_path):
    """Layered models carry updated state between layers while sharing one token position stream."""
    model = multilayer_model()
    path = tmp_path / "multi.json"
    path.write_text(json.dumps(model))
    chunks = ["", "na", "⚙", "µ", "x", "na"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 8, chunks=chunks)
    actual = run_qinfer(path, prompt, 8, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 8)


def test_final_logits_are_token_array_order_for_layered_model(tmp_path):
    """The score array follows vocabulary JSON order even when ids are sparse and generation ties by id."""
    model = multilayer_model()
    path = tmp_path / "ordered.json"
    path.write_text(json.dumps(model))
    expected = reference(model, "nana⚙", 5)
    actual = run_qinfer(path, "nana⚙", 5)
    assert_close(actual, expected)
    assert len(actual["logits"]) == len(model["tokens"])
    assert model["tokens"][0]["id"] != 0


def test_gated_layers_project_carry_before_normalized_scoring(tmp_path):
    """A model with gates, projected carries, RMS normalization, and sparse token ids follows the public contract."""
    model = projected_gated_model()
    path = tmp_path / "projected.json"
    path.write_text(json.dumps(model))
    chunks = ["", "to", "ne", "µ", "!", "tone"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 7, chunks=chunks)
    actual = run_qinfer(path, prompt, 7, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 7)


def test_generation_reuses_projected_normalized_model_state(tmp_path):
    """Repeated generation updates states but leaves normalization limited to score calculation."""
    model = projected_gated_model()
    path = tmp_path / "stateful-projected.json"
    path.write_text(json.dumps(model))
    short = run_qinfer(path, "tonexµ", 1)
    long = run_qinfer(path, "tonexµ", 6)
    expected_long = reference(model, "tonexµ", 6)
    assert_close(long, expected_long)
    assert long["tokens"][:1] == short["tokens"]
    assert long["logits"] != short["logits"]


def test_input_mixing_and_state_clipping_interact_across_layers(tmp_path):
    """Mixed layer inputs are clipped before becoming projected carry for the next layer."""
    model = mixed_clipped_model()
    path = tmp_path / "mixed-clipped.json"
    path.write_text(json.dumps(model))
    chunks = ["", "r", "ed", "!", "red", "e"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 8, chunks=chunks)
    actual = run_qinfer(path, prompt, 8, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 8)


def test_logits_keep_full_serialized_precision(tmp_path):
    """Logit JSON values preserve enough Float64 precision for audit comparisons."""
    model = mixed_clipped_model()
    path = tmp_path / "precision.json"
    path.write_text(json.dumps(model))
    actual = run_qinfer(path, "red!red", 4)
    expected = reference(model, "red!red", 4)
    assert_close(actual, expected)
    assert any(abs(a - round(a, 6)) > 1e-9 for a in actual["logits"])


def test_feedback_mixing_uses_prior_state_before_clipping(tmp_path):
    """Feedback-mixed layers reuse the saved prior state and clamp the mixed result."""
    model = feedback_head_model()
    path = tmp_path / "feedback.json"
    path.write_text(json.dumps(model))
    chunks = ["", "fl", "ux", "µ", "!", "flux", "f"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 9, chunks=chunks)
    actual = run_qinfer(path, prompt, 9, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 9)


def test_mixed_head_and_position_bias_affect_generation(tmp_path):
    """A mixed scoring head and position-indexed bias participate in token selection and final logits."""
    model = feedback_head_model()
    path = tmp_path / "head-position.json"
    path.write_text(json.dumps(model))
    short = run_qinfer(path, "fluxµ", 2)
    long = run_qinfer(path, "fluxµ", 7)
    expected_short = reference(model, "fluxµ", 2)
    expected_long = reference(model, "fluxµ", 7)
    assert_close(short, expected_short)
    assert_close(long, expected_long)
    assert long["tokens"][:2] == short["tokens"]
    assert long["logits"] != short["logits"]


def test_initialized_layers_residual_carry_and_weighted_readout_interact(tmp_path):
    """Initialized states, residual carry, clipping, and weighted readout jointly affect generation."""
    model = initialized_readout_model()
    path = tmp_path / "initialized-readout.json"
    path.write_text(json.dumps(model))
    chunks = ["", "i", "on", "µ", "!", "ion", "n"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 8, chunks=chunks)
    actual = run_qinfer(path, prompt, 8, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 8)


def test_promptless_generation_uses_state_initializers_and_readout_weights(tmp_path):
    """Promptless generation still begins from documented layer initializers and readout weights."""
    model = initialized_readout_model()
    path = tmp_path / "promptless-readout.json"
    path.write_text(json.dumps(model))
    initialized = run_qinfer(path, "", 6)
    expected_initialized = reference(model, "", 6)
    assert_close(initialized, expected_initialized)
    no_init = json.loads(json.dumps(model))
    for layer in no_init["layers"]:
        layer.pop("state_init", None)
    no_init.pop("readout_layers")
    no_init_path = tmp_path / "no-init.json"
    no_init_path.write_text(json.dumps(no_init))
    baseline = run_qinfer(no_init_path, "", 6)
    assert initialized["tokens"] != baseline["tokens"] or initialized["logits"] != baseline["logits"]


def test_layer_state_norm_changes_downstream_carry_and_readout(tmp_path):
    """Per-layer normalized states are stored before later layers consume their carry."""
    model = normed_carry_model()
    path = tmp_path / "normed-carry.json"
    path.write_text(json.dumps(model))
    chunks = ["", "r", "ay", "µ", "!", "ra", "y"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 9, chunks=chunks)
    actual = run_qinfer(path, prompt, 9, chunks=chunks)
    assert_close(actual, expected)
    assert actual == run_qinfer(path, prompt, 9)


def test_normed_state_is_distinct_from_score_normalization(tmp_path):
    """Layer state normalization persists across generated steps independently of root score normalization."""
    model = normed_carry_model()
    path = tmp_path / "normed-score.json"
    path.write_text(json.dumps(model))
    actual = run_qinfer(path, "rayµray", 7)
    expected = reference(model, "rayµray", 7)
    assert_close(actual, expected)
    without_state_norm = json.loads(json.dumps(model))
    for layer in without_state_norm["layers"]:
        layer.pop("state_norm", None)
    alt_path = tmp_path / "without-state-norm.json"
    alt_path.write_text(json.dumps(without_state_norm))
    alternate = run_qinfer(alt_path, "rayµray", 7)
    assert actual["tokens"] != alternate["tokens"] or actual["logits"] != alternate["logits"]


def test_token_shift_history_bias_and_repeat_penalty_interact(tmp_path):
    """Token-conditioned input shifts, history bias, and repeat penalties jointly affect scoring."""
    model = feedback_head_model()
    for layer_index, layer in enumerate(model["layers"]):
        layer["token_shift"] = {
            "zero": 0,
            "scale": 0.021 + layer_index * 0.004,
            "rows": [
                [0, 2, -1, 3],
                [1, -3, 2, 0],
                [-2, 1, 0, -4],
                [3, 0, -2, 1],
                [-1, 4, 1, -3],
                [2, -2, 3, 0],
            ],
        }
    model["history_bias"] = {
        "zero": 0,
        "scale": 0.017,
        "rows": [
            [0, 2, -1, 3, -2, 1],
            [1, -3, 2, 0, 4, -2],
            [-2, 1, 0, -4, 3, 2],
            [3, 0, -2, 1, -1, 4],
            [-1, 4, 1, -3, 2, 0],
            [2, -2, 3, 0, -4, 1],
        ],
    }
    model["repeat_penalty"] = 0.043
    path = tmp_path / "history-repeat.json"
    path.write_text(json.dumps(model))
    prompt = "fluxµfluxf"
    expected = reference(model, prompt, 10)
    actual = run_qinfer(path, prompt, 10)
    assert_close(actual, expected)

    without_history = json.loads(json.dumps(model))
    without_history.pop("history_bias")
    without_history.pop("repeat_penalty")
    for layer in without_history["layers"]:
        layer.pop("token_shift")
    alt_path = tmp_path / "without-history-repeat.json"
    alt_path.write_text(json.dumps(without_history))
    alternate = run_qinfer(alt_path, prompt, 10)
    assert actual["tokens"] != alternate["tokens"] or actual["logits"] != alternate["logits"]


def test_promptless_scoring_omits_history_bias_until_a_token_exists(tmp_path):
    """History bias is absent from the first promptless score step even when row zero is nonzero."""
    model = feedback_head_model()
    model["history_bias"] = {
        "zero": 0,
        "scale": 0.031,
        "rows": [
            [5, -3, 4, -2, 1, 0],
            [-4, 2, 1, 3, -1, 5],
            [3, 0, -5, 2, 4, -2],
            [1, -4, 3, 0, 5, -3],
            [-2, 5, 0, -4, 3, 1],
            [4, 1, -3, 5, -2, 0],
        ],
    }
    path = tmp_path / "promptless-history.json"
    path.write_text(json.dumps(model))
    expected = reference(model, "", 1)
    actual = run_qinfer(path, "", 1)
    assert_close(actual, expected)

    without_history = json.loads(json.dumps(model))
    without_history.pop("history_bias")
    alt_path = tmp_path / "promptless-without-history.json"
    alt_path.write_text(json.dumps(without_history))
    assert actual == run_qinfer(alt_path, "", 1)


def test_delayed_state_history_is_mixed_before_normed_carry(tmp_path):
    """Delayed per-layer history contributes before normalization and downstream carry."""
    model = delayed_history_model()
    path = tmp_path / "delayed-history.json"
    path.write_text(json.dumps(model))
    chunks = ["", "ra", "y", "µ", "!", "ray", "r", "ay"]
    prompt = "".join(chunks)
    expected = reference(model, prompt, 11, chunks=chunks)
    actual = run_qinfer(path, prompt, 11, chunks=chunks)
    assert_close(actual, expected)

    without_history = json.loads(json.dumps(model))
    for layer in without_history["layers"]:
        layer.pop("history_mix", None)
    alt_path = tmp_path / "without-delayed-history.json"
    alt_path.write_text(json.dumps(without_history))
    alternate = run_qinfer(alt_path, prompt, 11)
    assert actual["tokens"] != alternate["tokens"] or actual["logits"] != alternate["logits"]


def test_group_bias_uses_last_token_group_and_shared_position(tmp_path):
    """Group logits depend on the previous token group and shared prompt/generation position."""
    model = delayed_history_model()
    path = tmp_path / "group-bias.json"
    path.write_text(json.dumps(model))
    prompt = "rayµray!"
    expected = reference(model, prompt, 9)
    actual = run_qinfer(path, prompt, 9)
    assert_close(actual, expected)

    no_group = json.loads(json.dumps(model))
    no_group.pop("group_bias")
    no_group_path = tmp_path / "without-group-bias.json"
    no_group_path.write_text(json.dumps(no_group))
    alternate = run_qinfer(no_group_path, prompt, 9)
    assert actual["tokens"] != alternate["tokens"] or actual["logits"] != alternate["logits"]


def test_promptless_generation_omits_group_bias_until_a_token_exists(tmp_path):
    """Group bias is not applied to the first score step when no previous token exists."""
    model = delayed_history_model()
    path = tmp_path / "promptless-group.json"
    path.write_text(json.dumps(model))
    expected = reference(model, "", 1)
    actual = run_qinfer(path, "", 1)
    assert_close(actual, expected)

    no_group = json.loads(json.dumps(model))
    no_group.pop("group_bias")
    no_group_path = tmp_path / "promptless-without-group.json"
    no_group_path.write_text(json.dumps(no_group))
    assert actual == run_qinfer(no_group_path, "", 1)


def test_group_bias_is_omitted_when_last_token_has_no_group(tmp_path):
    """Tokens missing from the group map do not fall back to group zero."""
    model = delayed_history_model()
    model["group_bias"]["groups"].pop("6")
    path = tmp_path / "missing-group.json"
    path.write_text(json.dumps(model))
    prompt = "ra"
    assert tokenize(model, prompt) == [6]
    expected = reference(model, prompt, 1)
    actual = run_qinfer(path, prompt, 1)
    assert_close(actual, expected)

    no_group = json.loads(json.dumps(model))
    no_group.pop("group_bias")
    no_group_path = tmp_path / "missing-without-group.json"
    no_group_path.write_text(json.dumps(no_group))
    assert actual == run_qinfer(no_group_path, prompt, 1)


def test_transition_bias_uses_previous_token_id_after_group_adjustment(tmp_path):
    """Transition logits use the previous token id after group and calibration adjustments."""
    model = delayed_history_model()
    path = tmp_path / "transition-bias.json"
    path.write_text(json.dumps(model))
    prompt = "rayµray!ra"
    expected = reference(model, prompt, 10)
    actual = run_qinfer(path, prompt, 10)
    assert_close(actual, expected)

    no_transition = json.loads(json.dumps(model))
    no_transition.pop("transition_bias")
    no_transition_path = tmp_path / "without-transition-bias.json"
    no_transition_path.write_text(json.dumps(no_transition))
    alternate = run_qinfer(no_transition_path, prompt, 10)
    assert actual["tokens"] != alternate["tokens"] or actual["logits"] != alternate["logits"]
