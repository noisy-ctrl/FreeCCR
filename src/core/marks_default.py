"""The marks' default model, WRITTEN by tools/fit_marks_model.py: a
logistic per kind fitted to the rule's verdicts at Sensitivity 50
(spec/marks-learning.md). Don't edit by hand.

light: 2430 gated; s0 rule 23 model 21 both 18; s25 rule 31 model 29 both 26; s50 rule 46 model 44 both 44; s75 rule 64 model 59 both 56; s100 rule 90 model 85 both 76
dark: 1508 gated; s0 rule 39 model 34 both 34; s25 rule 58 model 50 both 49; s50 rule 77 model 74 both 72; s75 rule 112 model 96 both 91; s100 rule 156 model 152 both 145
"""

MODELS = {
    "light": {"MU": [2.18176, 0.838537, 0.183368, 4.3983, 1.49685, 0.0368281, 0.0825488],
              "SD": [0.213729, 0.437143, 0.137214, 0.863952, 0.643994, 0.901886, 0.134054],
              "W": [0.992112, 4.93885, -0.440732, 0.572846, -0.362455, 0.0963616, -0.0225194],
              "B": -15.0215},
    "dark": {"MU": [2.2446, 0.954661, 0.132928, 3.73136, 1.31219, 0.577992, 0.0109846],
              "SD": [0.286359, 0.572738, 0.124988, 1.22153, 0.860489, 2.11122, 0.0489085],
              "W": [1.05381, 6.33141, -0.407302, 0.121856, -0.351285, 0.676252, 0.0871007],
              "B": -13.5731},
}
