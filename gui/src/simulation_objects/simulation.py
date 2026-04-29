import os
import time
from collections import defaultdict

from influxdb import InfluxDBClient

from src.simulation_objects.cell import Cell
from src.simulation_objects.ue import Ue


class Simulation:

    def __init__(self, number_of_ues: int, number_of_cells: int, sim_id: int = None):
        self.db_client = InfluxDBClient(
            host=os.getenv("INFLUXDB_HOST"),
            port=int(os.getenv("INFLUXDB_PORT")),
            username=os.getenv("INFLUXDB_USERNAME"),
            password=os.getenv("INFLUXDB_PASSWORD"),
            database=os.getenv("INFLUXDB_DATABASE"),
        )
        self.simulation_start_time = 0
        self.host_data_dir = os.getenv("HOST_DATA_DIR", "/host_data")
        self.text_fallback_values = {}
        self.number_of_ues = number_of_ues
        self.number_of_cells = number_of_cells
        self.temp_number_of_ues = 0
        self.temp_number_of_cells = 0
        self.temp_count_different = 0
        self.temp_timestamp = ""
        self.ue_history = []
        self.cell_history = []
        self.starting_power = 0
        self.current_power = 0
        self.maxec = 0
        self.totalcurrec = 0
        self.simulation_status = 'on'
        if number_of_ues > 0 and number_of_cells > 0:
            self.max_x, self.max_y = self.get_charts_max_axis_value()
            self.ues, self.cells, sim_id_from_ue = self.get_simulation_data(self.number_of_ues, self.number_of_cells)
            self.ue_history.append(self.ues)
            self.cell_history.append(self.cells)
            if sim_id is not None:
                self.sim_id = sim_id
            else:
                self.sim_id = sim_id_from_ue

        else:
            self.max_x = 6000
            self.max_y = 6000
            self.ues = []
            self.cells = []
            self.sim_id = None



    def get_charts_max_axis_value(self):
        max_x = self.get_last_value_from_measurement('gnbs_x_0')
        if not max_x:
            max_x = 6000
        max_y = self.get_last_value_from_measurement('gnbs_y_0')
        if not max_y:
            max_y = 6000
        return (int(max_x), int(max_y))

    def get_simulation_data(self, number_of_ues: int, number_of_cells: int) -> (list[Ue], list[Cell], int):
        ues = []
        cells = []
        for ue_id in range(1, number_of_ues + 1):
            ue = Ue(
                ue_id=ue_id,
                x_position=self.get_last_value_from_measurement(f'ue_position_x_{ue_id}'),
                y_position=self.get_last_value_from_measurement(f'ue_position_y_{ue_id}'),
                type=self.get_last_value_from_measurement(f'ue_position_type_{ue_id}'),
                MMWave_Cell=int(self.get_last_value_from_measurement(f'ue_position_cell_{ue_id}') or 0),
                LTE_Cell=int(1),
                ErrTotalNbrDl=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.errtotalnbrdl.1.ueid'),
                DRB_BufferSize_Qos=self.get_last_value_from_measurement(f'ue_{ue_id}_drb.buffersize.qos.ueid'),
                RRU_PrbUsedDl=self.get_last_value_from_measurement(f'ue_{ue_id}_rru.prbuseddl'),
                TP_Combined_PDCP_ENDC_kbps=self.get_last_value_from_measurement(f'ue_{ue_id}_drb.uethpdlpdcpbased.ueid'),
                TP_Combined_RLC_ENDC_kbps=self.get_last_value_from_measurement(f'ue_{ue_id}_drb.uethpdl.ueid'),
                QosFlow_PdcpPduVolumeDl=self.get_last_value_from_measurement(
                    f'ue_{ue_id}_qosflow.pdcppduvolumedl_filter'),
                TotNbrDlInitial=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.totnbrdlinitial'),
                TotNbrDlInitial_16Qam=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.totnbrdlinitial.16qam'),
                TotNbrDlInitial_64Qam=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.totnbrdlinitial.64qam'),
                TotNbrDlInitial_Qpsk=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.totnbrdlinitial.qpsk.ueid'),
                TotNbrDl=self.get_last_value_from_measurement(f'ue_{ue_id}_tb.totnbrdl.1.ueid'),
                PDCP_PDU_Volume=self.get_last_value_from_measurement(
                    f'ue_{ue_id}_qosflow_pdcppduvolumedl_filter_ueid(txpdcppdubytesnrrlc)'),
                PdcpSduDelayDl_ms_x_0_1=self.get_last_value_from_measurement(
                    f'ue_{ue_id}_drb.pdcpsdudelaydl.ueid(pdcp latency)'),
                Qos_PDCP_PDU=self.get_last_value_from_measurement(
                    f'ue_{ue_id}_drb_pdcppdunbrdl_qos_ueid(txpdcppdunrrlc)'),
                PDCP_PDU=self.get_last_value_from_measurement(f'ue_{ue_id}_tot_pdcpsdunbrdl_ueid(txdlpackets)'),
                PDCP_Throughput_kbps=self.get_last_value_from_measurement(
                    f'ue_{ue_id}_drb.pdcpsdubitratedl.ueid(pdcpthroughput)'),
                Tx_Bytes=self.get_last_value_from_measurement(f'ue_{ue_id}_drb.pdcpsduvolumedl_filter.ueid(txbytes)'),
                L3servingSINR_dB=self.get_last_value_from_measurement(f'ue_{ue_id}_l3 serving sinr'),
                L3servingSINR_CellID=self.get_last_value_from_measurement(f'ue_position_cell_{ue_id}'),
                L3neighSINR_dB=self.get_max_value_from_multiple_measurements(f'ue_{ue_id}_l3 neigh sinr ', 8),
                L3neighSINR_CellId=self.get_max_value_from_multiple_measurements(
                    f'ue_{ue_id}_l3 neigh id ',
                    8,
                    '(cellid)'),
                DRB_Estab_Succ_5QI=self.get_last_value_from_measurement(f'ue_{ue_id}_drb.estabsucc.5qi.ueid')
            )
            ues.append(ue)
        for cell_id in range(1, number_of_cells + 1):
            cell_x, cell_y, cell_type = self.get_cell_position_and_type(cell_id)
            es_state, es_power, maxec,  totalcurrec= self.get_cell_es_state_and_power(cell_id)
            cell = Cell(
                cell_id=cell_id,
                x_position=cell_x,
                y_position=cell_y,
                type=cell_type,
                es_state=es_state,
                es_power=es_power,
                maxec = maxec,
                totalcurrec = totalcurrec,
                serving_sinr=self.get_last_value_from_measurement(f'cu-cp-cell-{cell_id}_l3 serving sinr'),
                ErrTotalNbrDl=self.get_last_value_from_measurement(f'du-cell-{cell_id}_tb.errtotalnbrdl.1.ueid'),
                MeanActiveUEsDownlink=self.get_last_value_from_measurement(f'du-cell-{cell_id}_drb.meanactiveuedl'),
                DRB_BufferSize_Qos=self.get_last_value_from_measurement(f'du-cell-{cell_id}_drb.buffersize.qos.ueid'),
                RRU_PrbUsedDl=self.get_last_value_from_measurement(f'du-cell_{cell_id}_rru.prbuseddl'),
                QosFlow_PdcpPduVolumeDl=self.get_last_value_from_measurement(
                    f'du-cell-{cell_id}_qosflow.pdcppduvolumedl_filter'),
                TotNbrDlInitial=self.get_last_value_from_measurement(f'du-cell-{cell_id}_tb.totnbrdlinitial'),
                TotNbrDlInitial_16Qam=self.get_last_value_from_measurement(
                    f'du-cell-{cell_id}_tb.totnbrdlinitial.16qam'),
                TotNbrDlInitial_64Qam=self.get_last_value_from_measurement(
                    f'du-cell-{cell_id}_tb.totnbrdlinitial.64qam'),
                TotNbrDlInitial_Qpsk=self.get_last_value_from_measurement(
                    f'du-cell-{cell_id}_tb.totnbrdlinitial.qpsk.ueid'),
                TotNbrDl=self.get_last_value_from_measurement(f'du-cell-{cell_id}_tb.totnbrdl.1.ueid'),
                dlPrbUsage_percentage=self.get_last_value_from_measurement(f'du-cell-{cell_id}_dlprbusage'),
                Cell_Average_Latency_ms_x_0_1=self.get_last_value_from_measurement(
                    f'cu-up-cell-{cell_id}_drb.pdcpsdudelaydl (cellaveragelatency)'),
                LTE_Cell_PDCP_Volume=self.get_last_value_from_measurement(
                    f'cu-up-cell-{cell_id}_m_pdcpbytesdl (celldltxvolume)'),
            )
            cells.append(cell)
            sim_id = self.get_last_value_from_measurement('ue_position_simid_1')
        return ues, cells, sim_id

    def set_ue_cell_number(self):
        n_number_of_g_cells, n_timestamp = self.get_last_count_from_measurement('gnbs_count')
        n_number_of_e_cells, n_timestamp = self.get_last_count_from_measurement('enbs_count')
        n_number_of_ues, n_timestamp = self.get_last_count_from_measurement('ue_position_count')
        # Fallback 1: read txt files directly and initialize immediately
        if n_number_of_ues is None:
            self._refresh_text_fallback()
            n_number_of_ues = self.text_fallback_values.get("ue_position_count")
            n_number_of_g_cells = self.text_fallback_values.get("gnbs_count")
            n_number_of_e_cells = self.text_fallback_values.get("enbs_count", 0)
            if n_number_of_ues is not None and n_number_of_g_cells is not None:
                self.number_of_ues = n_number_of_ues
                self.number_of_cells = (n_number_of_g_cells or 0) + (n_number_of_e_cells or 0)
                if self.number_of_ues > 0 and self.number_of_cells > 0:
                    self.max_x, self.max_y = self.get_charts_max_axis_value()
                    self.ues, self.cells, sim_id = self.get_simulation_data(self.number_of_ues, self.number_of_cells)
                    self.ue_history.append(self.ues)
                    self.cell_history.append(self.cells)
                    if self.sim_id is None:
                        self.sim_id = sim_id
                return
            n_timestamp = ""
        # Fallback 2: count from InfluxDB SHOW MEASUREMENTS and directly initialize
        if n_number_of_ues is None:
            try:
                result = self.db_client.query('SHOW MEASUREMENTS')
                measurements = [m['name'] for m in result.get_points()]
                ue_ids = set()
                g_cell_ids = set()
                e_cell_ids = set()
                for m in measurements:
                    if m.startswith('ue_position_x_'):
                        ue_ids.add(int(m.split('_')[-1]))
                    if m.startswith('gnbs_x_') and not m.endswith('_0'):
                        g_cell_ids.add(int(m.split('_')[-1]))
                    if m.startswith('enbs_x_') and not m.endswith('_0'):
                        e_cell_ids.add(int(m.split('_')[-1]))
                if ue_ids:
                    self.number_of_ues = len(ue_ids)
                    self.number_of_cells = len(g_cell_ids) + len(e_cell_ids)
                    if self.number_of_ues > 0 and self.number_of_cells > 0:
                        self.max_x, self.max_y = self.get_charts_max_axis_value()
                        self.ues, self.cells, sim_id = self.get_simulation_data(self.number_of_ues, self.number_of_cells)
                        self.ue_history.append(self.ues)
                        self.cell_history.append(self.cells)
                        if self.sim_id is None:
                            self.sim_id = sim_id
                    return
            except Exception:
                pass
        if n_number_of_g_cells is not None or n_number_of_e_cells is not None:
            if n_number_of_g_cells is None:
                n_number_of_g_cells = 0
            if n_number_of_e_cells is None:
                n_number_of_e_cells = 0
            self.temp_number_of_cells = n_number_of_g_cells + n_number_of_e_cells
        if n_timestamp is None: n_timestamp = ""
        if n_number_of_ues is not None:
            if self.temp_number_of_ues == n_number_of_ues and self.temp_timestamp != n_timestamp:
                self.temp_count_different += 1
            self.temp_number_of_ues = n_number_of_ues
            self.temp_timestamp = n_timestamp
            if self.temp_count_different >= 1:
                self.number_of_ues = self.temp_number_of_ues
                self.number_of_cells = self.temp_number_of_cells
                if self.number_of_ues > 0 and self.number_of_cells > 0:
                    self.max_x, self.max_y = self.get_charts_max_axis_value()
                    self.ues, self.cells, sim_id_from_ue = self.get_simulation_data(self.number_of_ues, self.number_of_cells)
                    self.ue_history.append(self.ues)
                    self.cell_history.append(self.cells)
                    if self.sim_id is not None:
                        self.sim_id = self.sim_id
                    else:
                        self.sim_id = sim_id_from_ue

    def _fallback_value(self, measurement_name: str):
        value = self.text_fallback_values.get(measurement_name)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    def get_last_value_from_measurement(self, measurement_name: str) -> float | str | int | None:
        try:
            result = self.db_client.query(f'SELECT LAST("value") FROM "{measurement_name}" where time > {self.simulation_start_time}')
            if result:
                points = list(result.get_points(measurement=measurement_name))
                value = points[0].get('last')
                if isinstance(value, float) and value.is_integer():
                    return int(value)
                return value
            else:
                return None
        except Exception as e:
            fallback = self._fallback_value(measurement_name)
            if fallback is not None:
                return fallback
            raise Exception(f"Error querying InfluxDB: {e}")

    def get_last_count_from_measurement(self, measurement_name: str):
        try:
            result = self.db_client.query(f'SELECT LAST("value") FROM "{measurement_name}" where time > {self.simulation_start_time}')
            if result:
                points = list(result.get_points(measurement=measurement_name))
                value = points[0].get('last')
                timestamp = points[0].get('time')
                if isinstance(value, float) and value.is_integer():
                    return (int(value), timestamp)
                return (value, timestamp)
            else:
                return (None, None)
        except Exception as e:
            raise Exception(f"Error querying InfluxDB: {e}")

    def get_first_value_from_measurement(self, measurement_name: str) -> float | str | int | None:
        try:
            result = self.db_client.query(f'SELECT FIRST("value") FROM "{measurement_name}" where time > {self.simulation_start_time}')
            if result:
                points = list(result.get_points(measurement=measurement_name))
                value = points[0].get('first')
                if isinstance(value, float) and value.is_integer():
                    return int(value)
                return value
            else:
                return None
        except Exception as e:
            raise Exception(f"Error querying InfluxDB: {e}")

    def get_max_value_from_multiple_measurements(
            self,
            measurement_name: str,
            ids: int,
            suffix='') -> float | int | None:
        values = []
        for i in range(1, ids + 1):
            if suffix != '':
                meas_name = f'{measurement_name}{i} {suffix}'
            else:
                meas_name = f'{measurement_name}{i}'
            new_value = self.get_last_value_from_measurement(meas_name)
            values.append(new_value)
        numerics = []
        for value in values:
            try:
                numeric_value = float(value)
                numerics.append(numeric_value)
            except (ValueError, TypeError):
                continue
        if numerics:
            max_value = max(numerics)
            return int(max_value) if max_value.is_integer() else max_value
        else:
            return None

    def get_cell_position_and_type(self, cell_id: int) -> (int, int, str):
        x_val = self.get_last_value_from_measurement(f'enbs_x_{cell_id}')
        if x_val:
            y_val = self.get_last_value_from_measurement(f'enbs_y_{cell_id}')
            cell_type = 'lte'
        else:
            x_val = self.get_last_value_from_measurement(f'gnbs_x_{cell_id}')
            y_val = self.get_last_value_from_measurement(f'gnbs_y_{cell_id}')
            cell_type = 'mmwave'

        return x_val, y_val, cell_type

    def get_cell_es_state_and_power(self, cell_id: int) -> (int, int, int, int):
        if cell_id == 1:
            es_state = self.get_last_value_from_measurement(f'enbs_esstate_{cell_id}')
            es_power = self.get_last_value_from_measurement(f'enbs_espower_{cell_id}')
            maxec = self.get_last_value_from_measurement(f'enbs_maxec_{cell_id}')
            totalcurrec = self.get_last_value_from_measurement(f'enbs_totalcurrec_{cell_id}')
        else:
            es_state = self.get_last_value_from_measurement(f'gnbs_esstate_{cell_id}')
            es_power = self.get_last_value_from_measurement(f'gnbs_espower_{cell_id}')
            maxec = self.get_last_value_from_measurement(f'gnbs_maxec_{cell_id}')
            totalcurrec = self.get_last_value_from_measurement(f'gnbs_totalcurrec_{cell_id}')
        return es_state, es_power, maxec, totalcurrec

    def _safe_read_lines(self, path):
        try:
            with open(path, encoding="utf-8") as file:
                return [line.strip() for line in file if line.strip()]
        except OSError:
            return []

    def _refresh_text_fallback(self):
        values = {}
        values.update(self._load_ue_position_fallback())
        values.update(self._load_gnbs_fallback())
        values.update(self._load_sinr_fallback())
        self.text_fallback_values = values

    def _load_ue_position_fallback(self):
        path = os.path.join(self.host_data_dir, "ue_position.txt")
        lines = self._safe_read_lines(path)
        if len(lines) <= 1:
            return {}
        rows = [line.split(",") for line in lines[1:] if len(line.split(",")) >= 7]
        if not rows:
            return {}
        latest_simid = rows[-1][6]
        latest_rows = {}
        for row in rows:
            if row[6] != latest_simid:
                continue
            latest_rows[int(row[1])] = row
        values = {"ue_position_count": len(latest_rows)}
        for ue_id, row in latest_rows.items():
            values[f"ue_position_x_{ue_id}"] = float(row[2])
            values[f"ue_position_y_{ue_id}"] = float(row[3])
            values[f"ue_position_type_{ue_id}"] = row[4]
            values[f"ue_position_cell_{ue_id}"] = int(float(row[5]))
            values[f"ue_position_simid_{ue_id}"] = int(float(row[6]))
        return values

    def _load_gnbs_fallback(self):
        path = os.path.join(self.host_data_dir, "gnbs.txt")
        lines = self._safe_read_lines(path)
        if len(lines) <= 1:
            return {}
        rows = [line.split(",") for line in lines[1:] if len(line.split(",")) >= 4]
        if not rows:
            return {}
        latest_cells = {}
        for row in rows:
            cell_id = int(float(row[1]))
            latest_cells[cell_id] = row
        mmwave_cells = [c for c in latest_cells if c != 0]
        values = {"gnbs_count": len(mmwave_cells)}
        for cell_id, row in latest_cells.items():
            if cell_id == 0:
                values["gnbs_x_0"] = float(row[2])
                values["gnbs_y_0"] = float(row[3])
                continue
            values[f"gnbs_x_{cell_id}"] = float(row[2])
            values[f"gnbs_y_{cell_id}"] = float(row[3])
            if len(row) >= 9:
                values[f"gnbs_esstate_{cell_id}"] = int(float(row[5]))
                values[f"gnbs_espower_{cell_id}"] = float(row[6])
                values[f"gnbs_maxec_{cell_id}"] = float(row[7])
                values[f"gnbs_totalcurrec_{cell_id}"] = float(row[8])
        return values

    def _load_sinr_fallback(self):
        latest_by_ue = {}
        cell_sinr_values = defaultdict(list)
        for cell_id in (2, 3, 4):
            path = os.path.join(self.host_data_dir, f"cu-cp-cell-{cell_id}.txt")
            lines = self._safe_read_lines(path)
            if len(lines) <= 1:
                continue
            for line in lines[1:]:
                row = line.split(",")
                if len(row) < 8:
                    continue
                try:
                    timestamp = int(float(row[0]))
                    ue_id = int(float(row[6]))
                    serving_sinr = float(row[7])
                except ValueError:
                    continue
                cell_sinr_values[cell_id].append(serving_sinr)
                prev = latest_by_ue.get(ue_id)
                if prev is None or timestamp >= prev["timestamp"]:
                    latest_by_ue[ue_id] = {"timestamp": timestamp, "sinr": serving_sinr, "cell_id": cell_id}
        values = {}
        for ue_id, item in latest_by_ue.items():
            values[f"ue_{ue_id}_l3 serving sinr"] = item["sinr"]
            values.setdefault(f"ue_position_cell_{ue_id}", item["cell_id"])
        for cell_id, sinrs in cell_sinr_values.items():
            if sinrs:
                values[f"cu-cp-cell-{cell_id}_l3 serving sinr"] = sum(sinrs) / len(sinrs)
        return values
