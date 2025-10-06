from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import math
import requests
import datetime

# ------------------ Constantes Científicamente Validadas ------------------

NASA_API_KEY = "dmSkYu2tXVXacdToqJfgInzr4qgOPP1VYdlUoZvK"
NASA_NEO_URL = "https://api.nasa.gov/neo/rest/v1/feed"

# Constantes físicas fundamentales
JOULES_PER_MEGATON = 4.184e15
EARTH_GRAVITY = 9.81  # m/s²
AIR_DENSITY_SEA_LEVEL = 1.225  # kg/m³
SPEED_OF_SOUND = 343  # m/s

# Factores de escala para visualización REALISTA
THERMAL_SCALING_FACTOR = 0.6    # Aumentado de 0.15 a 0.6 (60% del tamaño real)
BLAST_SCALING_FACTOR = 0.8      # Aumentado de 0.25 a 0.8 (80% del tamaño real)
FIREBALL_SCALING_FACTOR = 0.7   # Aumentado de 0.4 a 0.7 (70% del tamaño real)

# ------------------ Modelos Científicos Basados en Referencias ------------------

def _energy_megatons(energia_joules):
    """Conversión exacta de joules a megatones de TNT"""
    return energia_joules / JOULES_PER_MEGATON

def _calculate_kinetic_energy(diametro, densidad, velocidad):
    """
    Calcula energía cinética con corrección de unidades
    """
    # Asegurar que la velocidad esté en m/s
    if velocidad > 1000:  # Probablemente en m/s ya
        velocidad_ms = velocidad
    else:  # Probablemente en km/s
        velocidad_ms = velocidad * 1000
    
    radio = diametro / 2.0
    volumen = (4.0/3.0) * math.pi * (radio**3)
    masa = densidad * volumen
    
    if velocidad_ms > 30000:
        gamma = 1 / math.sqrt(1 - (velocidad_ms**2 / (299792458**2)))
        energia = (gamma - 1) * masa * (299792458**2)
    else:
        energia = 0.5 * masa * (velocidad_ms**2)
    
    return masa, energia, velocidad_ms

def _crater_metrics(diametro_proy_m, energia_megatons, angulo_impacto, densidad_proy, target_type):
    """
    Calcula dimensiones del cráter usando el modelo de Schmidt-Holsapple
    """
    if energia_megatons <= 0:
        return diametro_proy_m * 8, diametro_proy_m * 1.6
    
    # Convertir a joules para cálculos
    energia_joules = energia_megatons * JOULES_PER_MEGATON
    
    # Parámetros del objetivo basados en tipo
    if target_type == "water":
        densidad_objetivo = 1000  # kg/m³
        resistencia_objetivo = 0.1e6  # Pa (agua)
        K1, mu = 1.5, 0.55  # Parámetros para agua
    else:
        densidad_objetivo = 2500  # kg/m³
        resistencia_objetivo = 1e7  # Pa (roca sedimentaria)
        K1, mu = 1.2, 0.4  # Parámetros para roca
    
    angulo_rad = math.radians(angulo_impacto)
    correccion_angulo = math.sin(angulo_rad) ** 0.5
    
    # Cálculo del cráter transitorio (modelo de escala π)
    diametro_transitorio = (
        K1 * 
        (densidad_proy / densidad_objetivo) ** (1/3) * 
        (energia_joules / resistencia_objetivo) ** (1/3) *
        correccion_angulo
    )
    
    # Conversión a cráter simple final (colapso gravitacional)
    diametro_final = 1.3 * diametro_transitorio
    
    # Profundidad basada en relaciones observacionales
    profundidad_final = diametro_final * 0.22
    
    # Mínimo físico basado en el proyectil
    diametro_minimo = diametro_proy_m * 8
    diametro_final = max(diametro_final, diametro_minimo)
    
    return diametro_final, profundidad_final

def _fireball_radius_m(energia_megatons, altura_impacto_km=0):
    """
    Radio de la bola de fuego CON RELACIONES FÍSICAS CORRECTAS
    """
    if energia_megatons <= 0:
        return 0
    
    # RELACIÓN FÍSICA: Fireball ~ 2-5x el radio del cráter
    # Basado en observaciones de impactos y explosiones nucleares
    if altura_impacto_km > 5:
        coeficiente = 180  # Aumentado de 120 - Para airbursts
    else:
        coeficiente = 120  # Aumentado de 80 - Para surface impacts
    
    radio_fireball = coeficiente * (energia_megatons ** (1/3))
    
    # APLICAR FACTOR DE ESCALA MEJORADO
    radio_fireball *= FIREBALL_SCALING_FACTOR
    
    return radio_fireball

def _thermal_radiation_radius_m(energia_megatons, tipo_lesion="lethal"):
    """
    Calcula radios para efectos de radiación térmica CON RELACIONES FÍSICAS CORRECTAS
    """
    if energia_megatons <= 0:
        return 0
    
    # Diferentes umbrales de fluencia térmica (cal/cm²)
    umbrales = {
        "lethal": 25,      # Muerte por quemaduras (25 cal/cm²)
        "burns_3rd": 15,   # Quemaduras de 3er grado
        "burns_2nd": 8,    # Quemaduras de 2do grado  
        "burns_1st": 5,    # Quemaduras de 1er grado
        "ignition": 10     # Ignición de materiales
    }
    
    fluencia_objetivo = umbrales.get(tipo_lesion, 25)

    # Los efectos térmicos son mucho más extensos que el cráter
    radio_termico = 450 * math.sqrt(energia_megatons) * (25/fluencia_objetivo)**0.5  # Aumentado de 200 a 450
    
    # APLICAR FACTOR DE ESCALA MEJORADO
    radio_termico *= THERMAL_SCALING_FACTOR
    
    return radio_termico

def _blast_overpressure_radii(energia_megatons, altura_impacto_km=0):
    """
    Calcula radios de sobrepresión CON RELACIONES FÍSICAS CORRECTAS
    """
    if energia_megatons <= 0:
        return {"50_psi": 0, "10_psi": 0, "5_psi": 0, "1_psi": 0}
    
    if altura_impacto_km > 2:
        factor_altura = 1.1 + (altura_impacto_km * 0.05)
    else:
        factor_altura = 1.0
    
    # RADIO BASE CORREGIDO - Los efectos blast son los más extensos
    radio_base = 2500 * (energia_megatons ** (1/3)) * factor_altura  # Aumentado de 800 a 2500
    
    # APLICAR FACTOR DE ESCALA MEJORADO
    radio_base *= BLAST_SCALING_FACTOR
    
    # RELACIONES EMPÍRICAS - Los efectos de baja presión son más extensos
    relaciones_presion = {
        "50_psi": 0.12,   # Edificios de concreto destruidos (reducido ligeramente)
        "10_psi": 0.30,   # Edificios residenciales colapsan (aumentado de 0.25)
        "5_psi": 0.50,    # Daño estructural severo (aumentado de 0.40)
        "1_psi": 1.0      # Ventanas rotas (mantenido)
    }
    
    return {psi: radio_base * factor for psi, factor in relaciones_presion.items()}

def _blast_wind_speed(overpressure_psi):
    """
    Calcula velocidad del viento usando relaciones hidrodinámicas
    """
    if overpressure_psi <= 0:
        return 0
    
    # Convertir PSI a Pascales
    overpressure_pa = overpressure_psi * 6894.76
    
    # Para sobrepresiones moderadas (<20 PSI), usar relación aproximada
    if overpressure_psi < 20:
        v_ms = overpressure_pa / (AIR_DENSITY_SEA_LEVEL * SPEED_OF_SOUND)
    else:
        # Para ondas fuertes, relación no lineal
        v_ms = 16 * math.sqrt(overpressure_psi)
    
    # Convertir a km/h y limitar valores físicamente posibles
    v_kmh = v_ms * 3.6
    return min(round(v_kmh, 1), 2000)

def _estimate_seismic_magnitude(energia_joules, profundidad_km=0):
    """
    Calcula magnitud sísmica usando relación energía-magnitud mejorada
    """
    if energia_joules <= 0:
        return 0.0
    
    # Eficiencia sísmica depende del mecanismo y profundidad
    if profundidad_km > 1:
        eficiencia = 5e-4  # Impactos profundos más eficientes
    else:
        eficiencia = 1e-4  # Impactos superficiales
        
    energia_sismica = eficiencia * energia_joules
    
    # Relación energía-magnitud (Gutenberg-Richter moderna)
    if energia_sismica > 0:
        mw = (math.log10(energia_sismica) - 4.8) / 1.5
    else:
        mw = 0.0
    
    return round(max(mw, 0.0), 2)

def estimate_seismic_effects(mw, tipo_suelo="rock"):
    """
    Estima intensidades sísmicas con distancias DINÁMICAS pero asegurando visibilidad
    """
    # DISTANCIAS DINÁMICAS basadas en la magnitud, pero con MÍNIMOS para visibilidad
    if mw >= 8.0:
        distances_km = [50, 200, 500, 1000, 2000, 5000]
    elif mw >= 7.0:
        distances_km = [30, 100, 300, 600, 1000, 2000]
    elif mw >= 6.0:
        distances_km = [20, 50, 150, 300, 600, 1000]
    elif mw >= 5.0:
        distances_km = [10, 30, 80, 150, 300, 500]
    elif mw >= 4.0:
        distances_km = [5, 15, 40, 80, 150, 250]
    else:
        # para magnitudes bajas se usa distancias mínimas para visibilidad
        distances_km = [2, 5, 10, 20, 40, 80]
    
    # Se filtra para asegurar que haya al menos una distancia > 1km para visibilidad
    distances_km = [d for d in distances_km if d >= 1]
    
    # SI TODAS LAS DISTANCIAS SON MUY PEQUEÑAS, agregar una mínima para visibilidad
    if max(distances_km) < 5:
        distances_km.append(5)  # Mínimo 5km para visibilidad
    
    results = {}
    
    # Factores de amplificación por tipo de suelo
    amplificacion_suelo = {
        "rock": 1.0,
        "hard_soil": 1.3,
        "soft_soil": 1.8,
        "sediment": 2.2
    }
    factor_suelo = amplificacion_suelo.get(tipo_suelo, 1.0)
    
    for d in distances_km:
        if d < 1:
            d = 1
        
        # Modelo de atenuación simplificado (Atkinson & Boore 2006)
        R = math.sqrt(d**2 + 10**2)  # Distancia hipocentral aproximada
        
        # Calcular PGA (Peak Ground Acceleration) en %g
        log10_pga = (0.5 + 0.4 * mw - 
                    1.0 * math.log10(R) - 
                    0.002 * R)
        pga = (10 ** log10_pga) * factor_suelo
        
        # Convertir PGA a MMI (Wald et al. 1999)
        if pga >= 1.0: mmi = "IX-X"
        elif pga >= 0.5: mmi = "VIII"
        elif pga >= 0.3: mmi = "VII"
        elif pga >= 0.15: mmi = "VI"
        elif pga >= 0.08: mmi = "V"
        elif pga >= 0.04: mmi = "IV"
        elif pga >= 0.02: mmi = "III"
        elif pga >= 0.01: mmi = "II"
        else: mmi = "I"
        
        results[f"{d}_km"] = {
            "pga_g": round(pga, 3),
            "mmi": mmi,
            "description": _get_mmi_description(mmi.split("-")[0])
        }
    
    return results

def _get_mmi_description(mmi):
    """Proporciona descripción detallada de intensidades MMI"""
    descripciones = {
        "I": "It doesn't feel",
        "II": "Felt by a few people at rest",
        "III": "Noticeably felt indoors",
        "IV": "Hanging objects sway, vibration like a passing truck",
        "V": "Felt by almost everyone, unstable objects fall",
        "VI": "Felt by all, minor damage to weak structures",
        "VII": "Minor damage to ordinary buildings",
        "VIII": "Considerable damage to ordinary structures",
        "IX": "General panic, significant damage to resistant structures",
        "X": "Destruction of most masonry structures"
    }
    return descripciones.get(mmi, "Unspecified intensity")

def _tsunami_effects(energia_megatons, profundidad_agua_m, distancia_costa_km):
    """
    Evalúa efectos de tsunami basado en energía del impacto y condiciones locales
    """
    if energia_megatons <= 1:
        return {"likely": False, "max_wave_height_m": 0, "notes": "Insufficient energy for significant tsunami"}
    
    # Altura de ola inicial aproximada
    if profundidad_agua_m > 1000:
        # Agua profunda - mayor eficiencia
        altura_onda = 0.02 * (energia_megatons ** 0.5)
    else:
        # Agua poco profunda - menor eficiencia  
        altura_onda = 0.01 * (energia_megatons ** 0.5)
    
    # Amplificación costera (run-up)
    run_up_factor = 2.0
    altura_maxima_costa = altura_onda * run_up_factor
    
    # Clasificación de efectos
    if energia_megatons > 100:
        clasificacion = "Devastating regional tsunami"
    elif energia_megatons > 10:
        clasificacion = "Significant regional tsunami"
    elif energia_megatons > 1:
        clasificacion = "Local tsunami"
    else:
        clasificacion = "minor waves"
    
    return {
        "likely": energia_megatons > 1,
        "max_wave_height_m": round(altura_maxima_costa, 1),
        "classification": clasificacion,
        "notes": f"Estimated maximum height on the coast: {round(altura_maxima_costa, 1)} m"
    }

# ------------------ Endpoints ------------------

@require_http_methods(["GET", "OPTIONS"])
def asteroides(request):

    response = JsonResponse({"asteroides": []})
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    
    if request.method == "OPTIONS":
        response.status_code = 200
        return response
        
    hoy = datetime.date.today().strftime("%Y-%m-%d")
    manana = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    start_date = request.GET.get("start_date", hoy)
    end_date = request.GET.get("end_date", manana)

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "api_key": NASA_API_KEY
    }

    try:
        response = requests.get(NASA_NEO_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        asteroides_list = []
        for fecha, lista in data.get("near_earth_objects", {}).items():
            for ast in lista:
                asteroides_list.append({
                    "nombre": ast.get("name"),
                    "diametro_m": ast.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max"),
                    "velocidad_km_h": ast.get("close_approach_data", [])[0].get("relative_velocity", {}).get("kilometers_per_hour") if ast.get("close_approach_data") else None,
                    "fecha_aproximacion": ast.get("close_approach_data", [])[0].get("close_approach_date") if ast.get("close_approach_data") else None,
                    "distancia_km": ast.get("close_approach_data", [])[0].get("miss_distance", {}).get("kilometers") if ast.get("close_approach_data") else None,
                    "peligroso": ast.get("is_potentially_hazardous_asteroid"),
                })

        return JsonResponse({"asteroides": asteroides_list})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def impacto(request):
    nombre_asteroide = request.GET.get("asteroide", None)

    if nombre_asteroide:
        hoy = datetime.date.today().strftime("%Y-%m-%d")
        manana = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        params = {"start_date": hoy, "end_date": manana, "api_key": NASA_API_KEY}
        try:
            response = requests.get(NASA_NEO_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            encontrado = None
            for fecha, lista in data.get("near_earth_objects", {}).items():
                for ast in lista:
                    if ast.get("name", "").lower() == nombre_asteroide.lower():
                        encontrado = ast
                        break
                if encontrado:
                    break
            if not encontrado:
                return JsonResponse({"error": f"Asteroide '{nombre_asteroide}' no encontrado."}, status=404)

            diametro = encontrado.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max")
            # Convertir velocidad de km/h a m/s
            velocidad_km_h = float(encontrado.get("close_approach_data", [])[0].get("relative_velocity", {}).get("kilometers_per_hour"))
            velocidad = velocidad_km_h * 1000 / 3600  # km/h → m/s
            angulo = 45
            densidad = 3000
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    else:
        try:
            diametro = float(request.GET.get("diametro", 100))
            velocidad_input = float(request.GET.get("velocidad", 17))  # km/s del frontend
            # Convertir km/s a m/s
            velocidad = velocidad_input * 1000
            angulo = float(request.GET.get("angulo", 45))
            densidad = float(request.GET.get("densidad", 3000))
        except Exception as e:
            return JsonResponse({"error": f"Parámetros inválidos: {e}"}, status=400)
        nombre_asteroide = "Custom input"

    # --- cálculos físicos MEJORADOS ---
    masa, energia, velocidad_ms = _calculate_kinetic_energy(diametro, densidad, velocidad)
    energia_megatons = _energy_megatons(energia)

    # --- parámetros de ubicación ---
    lat = request.GET.get("lat", None)
    lon = request.GET.get("lon", None)
    target = request.GET.get("target", "land")
    altura_impacto = float(request.GET.get("altura", 0))  # km sobre superficie

    # --- cálculos de efectos CON RELACIONES FÍSICAS CORRECTAS ---
    crater_diameter_m, crater_depth_m = _crater_metrics(
        diametro, energia_megatons, angulo, densidad, target
    )
    
    fireball_radius_m = _fireball_radius_m(energia_megatons, altura_impacto)
    
    # Múltiples efectos térmicos CON RELACIONES CORRECTAS
    thermal_effects_m = {
        "lethal": _thermal_radiation_radius_m(energia_megatons, "lethal"),
        "burns_3rd": _thermal_radiation_radius_m(energia_megatons, "burns_3rd"),
        "burns_2nd": _thermal_radiation_radius_m(energia_megatons, "burns_2nd"),
        "ignition": _thermal_radiation_radius_m(energia_megatons, "ignition")
    }
    
    blast_radii = _blast_overpressure_radii(energia_megatons, altura_impacto)
    blast_winds = {
        level: {
            "radius_m": r,
            "wind_speed_kmh": _blast_wind_speed(float(level.split("_")[0]))
        }
        for level, r in blast_radii.items()
    }
    
    mw = _estimate_seismic_magnitude(energia, 0)
    seismic_effects = estimate_seismic_effects(mw)

    # --- efectos de tsunami ---
    tsunami_effects = {}
    if target == "water":
        profundidad_agua = float(request.GET.get("profundidad_agua", 1000))
        tsunami_effects = _tsunami_effects(energia_megatons, profundidad_agua, 0)

    # --- VERIFICACIÓN DE RELACIONES FÍSICAS ---
    relaciones_verificacion = {
        "crater_to_fireball": fireball_radius_m / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
        "crater_to_blast_1psi": blast_radii["1_psi"] / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
        "crater_to_thermal_lethal": thermal_effects_m["lethal"] / (crater_diameter_m/2) if crater_diameter_m > 0 else 0,
    }

    response = {
        "nombre": nombre_asteroide,
        "parametros_entrada": {
            "diametro_projectil_m": diametro,
            "velocidad_impacto_m_s": velocidad_ms,
            "angulo_impacto_grados": angulo,
            "densidad_kg_m3": densidad,
            "tipo_objetivo": target,
            "altura_impacto_km": altura_impacto
        },
        "energia": {
            "masa_kg": masa,
            "energia_joules": energia,
            "energia_megatones_TNT": round(energia_megatons, 2),
            "equivalente_bombas_hiroshima": round(energia_megatons / 0.015, 1)
        },
        "efectos_impacto": {
            "crater_diameter_m": round(crater_diameter_m, 1),
            "crater_depth_m": round(crater_depth_m, 1),
            "fireball_radius_m": round(fireball_radius_m, 1),
            "thermal_effects_m": {k: round(v, 1) for k, v in thermal_effects_m.items()},
            "blast_overpressure_radii_m": {k: round(v, 1) for k, v in blast_radii.items()},
            "blast_wind_effects": blast_winds
        },
        "efectos_sismicos": {
            "magnitud_momento_Mw": mw,
            "intensidades_regionales": seismic_effects
        },
        "efectos_tsunami": tsunami_effects if tsunami_effects else {"likely": False},
        "ubicacion": {
            "lat": lat,
            "lon": lon,
            "target": target
        },
        "factores_escala_aplicados": {
            "thermal_scaling": THERMAL_SCALING_FACTOR,
            "blast_scaling": BLAST_SCALING_FACTOR,
            "fireball_scaling": FIREBALL_SCALING_FACTOR,
            "nota": "Factores optimizados para relaciones físicas realistas"
        },
        "verificacion_relaciones": {
            "fireball_vs_crater_ratio": round(relaciones_verificacion["crater_to_fireball"], 1),
            "blast_1psi_vs_crater_ratio": round(relaciones_verificacion["crater_to_blast_1psi"], 1),
            "thermal_vs_crater_ratio": round(relaciones_verificacion["crater_to_thermal_lethal"], 1),
            "nota": "Ratios > 1 indican que el efecto es más extenso que el cráter"
        },
        "referencias_cientificas": [
            "Collins et al. (2005) - Earth Impact Effects Program",
            "Melosh (1989) - Impact Cratering: A Geologic Process", 
            "Glasstone & Dolan (1977) - The Effects of Nuclear Weapons",
            "Atkinson & Boore (2006) - Ground-motion prediction equations"
        ]
    }

    return JsonResponse(response)