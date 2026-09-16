import numpy as np
from nn_inference_runtime import InferenceSession

session = InferenceSession.from_config_file(
    config_path="model_conpiler/inference_10MAD11FT013_014_MAX_2023.config.json",
    model_path="model_conpiler/10MAD11FT013_014_MAX_2023_nn-template_15.keras",
    metadata_path="model_conpiler/10MAD11FT013_014_MAX_2023_nn-template_15_meta.json",
)

# --- метаданные ---
md = session.metadata()
print(md['project_code'])             # '10MAD11CT012.v2'
print(md['inputs'][1]['dimension'])   # 'Гц'

# --- цепочка ---
print(session.chain_summary())
for step in session.describe_chain():
    print(step['step'], step['type'], step['params'])

## --- инференс ---
#signals = {
#    "10MAD11CT012A§§XQ01": arr_xq01,   # каждый arr_* имеет форму (N, 2)
#    "Signal_A": arr_a,
#    "Signal_B": arr_b,
#    "Signal_C": arr_c,
#    "Signal_D": arr_d,
#}
#session.validate_signals(signals)    # опционально: явная проверка с понятной ошибкой
#y = session.predict(signals)         # np.ndarray