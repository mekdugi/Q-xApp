import os
import subprocess
from dataclasses import asdict

import requests
import json

import paramiko
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from src.simulation_objects.simulation import Simulation
from src.simulation_objects.simulation_manager import SimulationManager

influx_data_router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def get_simulation() -> Simulation:
    return SimulationManager.get_simulation()


@influx_data_router.get("/")
async def root(request: Request, simulation: Simulation = Depends(get_simulation)):
    host_ns3 = os.getenv('NS3_HOST')
    return templates.TemplateResponse(
        "chart.html",
        {
            "request": request,
            "ues": simulation.ues,
            "cells": simulation.cells,
            "sim_id": simulation.sim_id,
            "chart_dimensions": (simulation.max_x, simulation.max_y),
            "host_ns3": host_ns3,
        },
    )

@influx_data_router.get("/scenarios")
async def scenarios(request: Request):
    remote_host = os.getenv('NS3_HOST')
    files = {"0":"scratch/scenario-zero-with_parallel_loging.cc",
        "1":"scratch/scenario-one.cc",
        "2":"scratch/scenario-zero.cc"}
    try:
        response = requests.get(f'http://{remote_host}:38866', timeout=1.5)
        if response.status_code == 200:
            files = json.loads(response.text)
    except Exception:
        pass
    return files

@influx_data_router.get("/refresh-data")
async def refresh_data(request: Request, simulation: Simulation = Depends(get_simulation)):
    SimulationManager.refresh_simulation()
    updated_simulation = SimulationManager.get_simulation()
    if (updated_simulation.number_of_ues == 0 or updated_simulation.number_of_cells == 0) and updated_simulation.simulation_status == 'on':
        updated_simulation.set_ue_cell_number()
    es_state = {}
    sinr = {}
    retx = {}
    prb = {}
    for cell in updated_simulation.cells:
        es_state[cell.cell_id] = cell.es_state
        prb[cell.cell_id] = cell.dlPrbUsage_percentage
    for ue in updated_simulation.ues:
        sinr[ue.ue_id] = ue.L3servingSINR_dB
        retx[ue.ue_id] = ue.ErrTotalNbrDl
    print(updated_simulation.ues)
    return {
        "ues": [asdict(ue) for ue in updated_simulation.ues],
        "cells": [asdict(cell) for cell in updated_simulation.cells],
        "max_x_max_y": (updated_simulation.max_x, updated_simulation.max_y),
        "sim_id": updated_simulation.sim_id if updated_simulation.sim_id else 'off',
        "es_state": es_state,
        "sinr": sinr,
        "retx": retx,
        "prb": prb,
        "starting_power": updated_simulation.starting_power,
        "current_power": updated_simulation.current_power,
        "maxec": updated_simulation.maxec,
        "totalcurrec": updated_simulation.totalcurrec,
        "simulation_status": updated_simulation.simulation_status,
    }


@influx_data_router.post("/start_simulation")
async def start_simulation(request: Request):
    form_data = await request.json()
    SimulationManager.reset_simulation()
    remote_host = os.getenv('NS3_HOST')
    if not remote_host:
        print("NS3_HOST environment variable is not set.")
        return JSONResponse({"status": "error", "message": "NS3_HOST not set"}, status_code=500)
    fields = [
        "e2TermIp",
        "hoSinrDifference",
        "indicationPeriodicity",
        "simTime",
        "KPM_E2functionID",
        "RC_E2functionID",
        "N_MmWaveEnbNodes",
        #"N_LteEnbNodes",
        "N_Ues",
        "CenterFrequency",
        "Bandwidth",
        "N_AntennasMcUe",
        "N_AntennasMmWave",
        "IntersideDistanceUEs",
        "IntersideDistanceCells"
    ]
    scenario = form_data.get('scenario')
    if not scenario:
        return JSONResponse({"status": "error", "message": "No scenario specified"}, status_code=400)
    flags = False
    if form_data.get('flags') == 'true':
        flags = True
    if form_data.get('flexric') == 'true':
        arguments = ' '
    else:
        arguments = '--enableE2FileLogging=1 '
    for field in fields:
        value = form_data.get(field)
        if value is not None:
            arguments += f"--{field}={value} "
        elif value is None and field == 'simTime':
            arguments += f"--simTime=100 "
    if flags:
        command = f'./ns3 run "{scenario} {arguments}"'
    else:
        command = f'./ns3 run "{scenario}"'
    command = f'curl -X POST -d \'{command}\' http://{remote_host}:38866'
    try:
        print(f'Sending start command: {command}')
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        print("Response from server:")
        print(result.stdout)
        scenario = os.path.split(scenario)[1].split(".")[0]
        SimulationManager.start_simulation(scenario)
    except Exception as e:
        print(f"An error occurred: {e}")
        return JSONResponse({"status": "error", "message": str(e)})
    number_of_ues = int(form_data.get('N_Ues', 2))
    number_of_cells = int(form_data.get('N_LteEnbNodes', 1)) + int(form_data.get('N_MmWaveEnbNodes', 4))
    if not flags:
        number_of_ues = 0
        number_of_cells = 0
    SimulationManager._simulation = Simulation(number_of_ues, number_of_cells)
    return {"status": "started", "scenario": scenario}



@influx_data_router.post("/reset_simulation")
async def reset_simulation():
    SimulationManager.reset_simulation()
    return {"message": "Simulation reset"}


@influx_data_router.post("/stop_simulation")
async def stop_simulation():
    remote_host = os.getenv('NS3_HOST')
    scenario = SimulationManager.get_scenario()
    if not scenario:
        return    
    if not remote_host:
        print("NS3_HOST environment variable is not set.")
        return

    command = f"curl -X POST -d '{scenario}' http://{remote_host}:38867"

    try:
        print(f'Sending stop command: {command}')
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        print("Response from server:")
        print(result.stdout)
        SimulationManager.stop_simulation()
    except Exception as e:
        print(f"An error occurred: {e}")




@influx_data_router.post("/switch_usecase")
async def switch_usecase(request: Request):
    body = await request.json()
    mode = body.get("mode", "ts")
    mode_file = "/host_data/xapp_mode.txt"
    with open(mode_file, "w") as f:
        f.write(mode)
    return {"status": "ok", "mode": mode}


@influx_data_router.get("/current_usecase")
async def current_usecase():
    mode_file = "/host_data/xapp_mode.txt"
    try:
        with open(mode_file, "r") as f:
            mode = f.read().strip()
    except Exception:
        mode = "ts"
    return {"mode": mode}



@influx_data_router.get("/qxapp-result")
async def qxapp_result():
    import json
    result_path = "/host_data/qxapp_result.json"
    try:
        with open(result_path, "r") as f:
            return json.load(f)
    except Exception:
        return []




@influx_data_router.post("/kill_simulation")
async def kill_simulation():
    """Kill all simulation processes (RIC, ns-3, xApp)"""
    import subprocess
    commands = [
        "sudo pkill -9 -f nearRT-RIC",
        "sudo pkill -9 -f xapp_qxapp",
        "pkill -9 -f scenario-zero",
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
        except Exception:
            pass
    return {"status": "killed"}

@influx_data_router.post("/set_a1_policy")
async def set_a1_policy(request: Request):
    body = await request.json()
    max_ue = body.get("max_ue_per_cell", 2)
    policy_file = "/host_data/xapp_a1_policy.txt"
    with open(policy_file, "w") as f:
        f.write(str(max_ue))
    return {"status": "ok", "max_ue_per_cell": max_ue}


@influx_data_router.post("/set_sleep_config")
async def set_sleep_config(request: Request):
    body = await request.json()
    sleep_cells = body.get("sleep_cells", [])
    config_file = "/host_data/xapp_sleep_config.txt"
    with open(config_file, "w") as f:
        f.write(",".join(str(c) for c in sleep_cells))
    return {"status": "ok", "sleep_cells": sleep_cells}

@influx_data_router.post("/set_qos_config")
async def set_qos_config(request: Request):
    body = await request.json()
    weights = body.get("weights", [2.0, 2.0, 1.0, 1.0])
    config_file = "/host_data/xapp_qos_config.txt"
    with open(config_file, "w") as f:
        f.write(",".join(str(w) for w in weights))
    return {"status": "ok", "weights": weights}
