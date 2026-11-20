"""IR dust's default model (spec/ir-dust-v2.md §3.3), WRITTEN by
tools/fit_ir_dust_model.py from tools/ir_dust_labels.json: re-run it,
never edit by hand.

Fitted on 185 hand-checked candidates (59 dust) from 4 frames;
held out by frame: dust 46/59 found, 7/126 false.

p = 1 / (1 + exp(-(B + sum(W * (x - MU) / SD)))), x in ir_dust.FEATURES order.
"""

FEATURES = ('IR depth', 'line', 'visible dip', 'same shape', 'IR/visible', 'picture edge', 'log area', 'visible deepest')
MU = (4.88938, 4.87874, 1.30548, 0.283512, 0.274533, 0.999527, 3.33384, 5.77463)
SD = (0.732331, 1.55801, 3.19555, 0.331534, 0.412326, 0.966973, 0.675334, 7.84109)
W = (0.710124, 0.660612, 0.208511, 0.998883, 0.179145, -0.352749, -0.0626679, 1.63059)
B = -0.936749
