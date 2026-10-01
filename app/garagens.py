"""Garagens: vaga de cada apartamento conforme a convenção registrada (art. 4º, item 4.1; certidão do Registro Auxiliar nº 935)
e normalização da placa de veículo. Dos números 1 a 407 ficam sem apartamento os 7 de visitantes e 404 a 407 (3 PNE e zelador).
Na certidão, o apto 001 dos blocos 03 e 04 sai impresso com o número do bloco anterior; aqui vale a sequência da tabela."""
import re

APTOS = [f"{andar}{pos:02d}" for andar in range(5) for pos in range(1, 5)]  # 001..004, 101..104, 201..204, 301..304, 401..404
VISITANTES = {96, 97, 98, 124, 125, 126, 222}
ULTIMA = 407  # total de vagas do estacionamento

# bloco -> números das garagens na ordem dos apartamentos (blocos 01 a 12 só têm térreo e 1º pavimento)
_TABELA = {
    "01": "1 20 4 23 2 21 3 22", "02": "7 24 8 27 5 25 6 26", "03": "11 28 31 51 9 29 10 30", "04": "12 32 15 35 13 33 14 34",
    "05": "16 36 19 39 17 37 18 38", "06": "40 60 43 63 41 61 42 62", "07": "44 64 47 67 45 65 46 66", "08": "48 68 71 72 49 69 50 70",
    "09": "52 75 55 76 53 73 54 74", "10": "56 79 59 87 57 77 58 78", "11": "88 90 81 123 80 89 82 91", "12": "83 92 84 95 85 93 86 94",
    "13": "99 100 101 102 103 104 105 106 140 139 138 137 136 129 130 131 135 134 133 132",
    "14": "122 121 120 119 107 141 108 142 109 110 143 144 118 117 116 115 114 113 112 111",
    "15": "127 128 153 154 155 156 157 158 159 160 161 162 188 189 195 194 193 192 191 190",
    "16": "182 181 180 179 178 163 164 165 166 167 168 169 177 176 175 174 173 172 171 170",
    "17": "145 146 147 148 149 150 151 152 183 184 185 186 196 197 198 199 200 201 202 203",
    "18": "187 212 213 214 215 216 217 218 219 220 252 253 254 261 260 259 255 256 257 258",
    "19": "221 223 224 225 226 227 204 205 206 207 208 209 210 211 228 229 230 231 232 233",
    "20": "286 287 234 235 236 237 238 239 240 241 242 243 244 251 250 249 248 247 246 245",
    "21": "323 322 321 320 319 318 317 316 315 314 313 312 294 295 288 289 290 291 292 293",
    "22": "311 310 309 308 307 276 275 274 273 272 271 270 269 268 267 266 265 264 263 262",
    "23": "284 283 282 281 280 279 278 277 296 297 298 299 300 301 302 285 304 305 306 303",
    "24": "403 402 401 400 399 398 324 325 326 327 352 353 354 361 360 359 355 356 357 358",
    "25": "341 340 339 338 337 336 335 334 333 332 331 330 329 328 362 363 364 365 366 367",
    "26": "397 351 350 349 348 347 346 345 344 343 342 396 395 394 393 392 391 390 389 388",
    "27": "368 369 370 371 372 387 386 385 373 374 375 376 377 378 379 380 381 382 383 384",
}
CONVENCAO: dict[tuple[str, str], int] = {(bloco, apto): int(n) for bloco, nums in _TABELA.items() for apto, n in zip(APTOS, nums.split())}


_SO_LETRA, _SO_NUMERO = str.maketrans("01", "OI"), str.maketrans("OI", "01")


def limpar_placa(texto: str) -> str:
    """O que foi digitado, só com letras e números, em maiúsculas."""
    return re.sub(r"[^A-Za-z0-9]", "", texto or "").upper()


def normalizar_placa(texto: str) -> str | None:
    """Placa sem traço e em maiúsculas (ABC1234 ou, no padrão Mercosul, ABC1D23); None se não for uma placa.
    Desfaz a troca comum entre a letra O e o zero (e entre I e 1) onde só um deles é possível: as 3 primeiras posições
    são sempre letras; a 4ª, a 6ª e a 7ª são sempre números. A 5ª fica como foi digitada (pode ser letra ou número)."""
    placa = limpar_placa(texto)
    if len(placa) == 7:
        placa = placa[:3].translate(_SO_LETRA) + placa[3].translate(_SO_NUMERO) + placa[4] + placa[5:].translate(_SO_NUMERO)
    return placa if re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]\d{2}", placa) else None


if __name__ == "__main__":
    numeros = list(CONVENCAO.values())
    assert len(numeros) == 12 * 8 + 15 * 20 == 396 and len(set(numeros)) == 396  # uma garagem por apartamento, sem repetição
    assert all(len(nums.split()) == (8 if int(b) <= 12 else 20) for b, nums in _TABELA.items())
    assert set(range(1, ULTIMA + 1)) - set(numeros) == VISITANTES | {404, 405, 406, 407}  # confere com o art. 2º
    assert CONVENCAO["01", "001"] == 1 and CONVENCAO["03", "001"] == 11 and CONVENCAO["04", "001"] == 12
    assert CONVENCAO["17", "004"] == 148 and CONVENCAO["12", "104"] == 94 and CONVENCAO["27", "404"] == 384
    assert normalizar_placa("abc-1234") == "ABC1234" and normalizar_placa("ABC1D23") == "ABC1D23"
    assert normalizar_placa("AB1234") is None and normalizar_placa("ABCD123") is None and normalizar_placa("") is None
    assert normalizar_placa("RWV0C81") == normalizar_placa("rwvOc81") == normalizar_placa("RWV-0C8I") == "RWV0C81"  # O por zero, I por 1
    assert normalizar_placa("0AB1234") == "OAB1234" and normalizar_placa("ABC1O23") == "ABC1O23" and normalizar_placa("ABC1023") == "ABC1023"
    print("garagens ok")
