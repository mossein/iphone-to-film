"""
Wrapper around spectral_film_lut for photochemical negative→print conversion.
"""

import spectral_film_lut as sfl
from spectral_film_lut.film_spectral import FilmSpectral


def make_conversion(neg_data, print_data, exposure_kelvin=5500, exp_comp=0,
                    sat=1.0, pre_flash_neg=-4, pre_flash_print=-4,
                    black_offset=0, white_point=1.0, tint=0):
    neg = FilmSpectral(neg_data)
    prt = FilmSpectral(print_data) if print_data else None
    conv = FilmSpectral.generate_conversion(
        negative_film=neg, print_film=prt,
        input_colourspace="sRGB", output_colourspace="sRGB",
        projector_kelvin=6500, exposure_kelvin=exposure_kelvin,
        exp_comp=exp_comp, gamut_compression=0.2, sat_adjust=sat,
        mode="full" if prt else "negative",
        pre_flash_neg=pre_flash_neg, pre_flash_print=pre_flash_print,
        black_offset=black_offset, white_point=white_point, tint=tint,
    )
    return conv, neg


# Available print stocks for recipe mixing
PRINT_STOCKS = {
    "kodak_2383": {"name": "Kodak 2383 (Standard Cinema)", "data": sfl.KODAK_2383},
    "kodak_2393": {"name": "Kodak 2393 (Low Contrast)", "data": sfl.KODAK_2393},
    "fuji_3513": {"name": "Fuji 3513 (Fuji Cinema)", "data": sfl.FUJI_3513},
    "fuji_3523": {"name": "Fuji 3523 (Fuji Cinema Alt)", "data": sfl.FUJI_3523},
    "fuji_ca_dpii": {"name": "Fuji Crystal Archive DPII", "data": sfl.FUJI_CA_DPII},
    "kodak_endura": {"name": "Kodak Endura Premier", "data": sfl.KODAK_ENDURA_PREMIER},
    "fujiflex": {"name": "Fujiflex (Glossy Print)", "data": sfl.FUJIFLEX_NEW},
}
