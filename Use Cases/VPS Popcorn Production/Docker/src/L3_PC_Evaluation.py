import os
from time import sleep
from datetime import datetime
# from dataclasses import dataclass

import pandas as pd
import numpy as np

from classes.KafkaPC import KafkaPC
from classes.ml_util import ObjectiveFunction

pd.set_option("display.max_columns", None)
pd.options.display.float_format = "{:.3f}".format


class CognitionPC(KafkaPC):
    def __init__(self, config_path, config_section):
        super().__init__(config_path, config_section)

        df_columns = [
            "phase",
            "model_name",
            "id_x",
            "n_data_points",
            "id_start_x",
            "model_size",
            "best_x",
            "best_pred_y",
            "best_pred_y_norm",
            "y",
            "y_norm",
            "y_delta",
            "rmse",
            "rmse_norm",
            "mae",
            "rsquared",
            "CPU_ms",
            "CPU_norm",
            "RAM",
            "RAM_norm",
            "pred_quality",
            "real_quality",
            "resources",
            "overall_quality",
            "timestamp",
        ]

        self.df = pd.DataFrame(columns=df_columns)

        self.current_data_point = 0

        """initialize objective function"""
        self.new_objective = ObjectiveFunction()
        self.new_objective.load_data()
        self.new_objective.fit_model()

        self.N_INITIAL_DESIGN = self.config['N_INITIAL_DESIGN']
        self.X_MIN = self.config['X_MIN']
        self.X_MAX = self.config['X_MAX']
        self.generate_initial_design(self.N_INITIAL_DESIGN, self.X_MIN, self.X_MAX)
        self.selected_algo = {'selected_algo_id': None,
                              'selected_algo': None,
                              'selected_x': None,
                              }

        # maps topics and functions, which process the incoming data
        self.func_dict = {
            "AB_model_application": self.process_model_application,
            "AB_monitoring": self.process_monitoring,
        }

    def generate_initial_design(self, n_initial_design, x_min, x_max):
        """ number n_initial_design equally spaced X between X_MIN, X_MAX """

        self.X = np.linspace(x_min, x_max, num=n_initial_design)

    def strategy_min_best_pred_y(self):
        min_best_pred_y = self.df.loc[self.df["best_pred_y"].idxmin()]
        selected_algo_id = self.df["best_pred_y"].idxmin()
        selected_algo = min_best_pred_y['model_name']
        best_x = min_best_pred_y['best_x']

        return selected_algo_id, selected_algo, best_x

    def strategy_model_quality(self):
        df_pred_y = self.df[self.df.y.isnull()].copy()
        df_real_y = self.df[self.df.y.notnull()].copy()

        df_pred_y['pred_quality'] = df_pred_y['pred_quality'] * self.config['USER_WEIGHTS']['QUALITY']
        df_pred_y['resources'] = df_pred_y['resources'] * self.config['USER_WEIGHTS']['RESOURCES']
        df_real_y['real_quality'] = df_real_y['real_quality'] * self.config['USER_WEIGHTS']['QUALITY']
        df_real_y['resources'] = df_real_y['resources'] * self.config['USER_WEIGHTS']['RESOURCES']

        df_pred_y['overall_quality'] = df_pred_y.apply(lambda row: self.calc_avg((row['pred_quality'],
                                                                                  row['resources'])), axis=1)

        df_real_y['overall_quality'] = df_real_y.apply(lambda row: self.calc_avg((row['real_quality'],
                                                                                  row['resources'])), axis=1)
        combined_df = pd.concat([df_pred_y, df_real_y], axis=0, sort=False)
        best_quality = combined_df.loc[combined_df['overall_quality'].idxmin()]

        selected_algo_id = best_quality.name
        selected_algo = best_quality['model_name']
        best_x = best_quality['best_x']

        return selected_algo_id, selected_algo, best_x

    def get_best_x(self, strategy="model_quality"):

        selected_algo_id = None
        selected_algo = None
        best_x = None

        if strategy == 'min_best_pred_y':
            selected_algo_id, selected_algo, best_x = self.strategy_min_best_pred_y()

        elif strategy == 'model_quality':
            selected_algo_id, selected_algo, best_x = self.strategy_model_quality()

        return selected_algo_id, selected_algo, best_x

    def send_new_x(self, x):
        new_x = {
            "new_x": x,
        }
        self.send_msg(new_x)

    def calc_y_delta(self, row):
        return abs(row['y'] - row['best_pred_y'])

    def calc_avg(self, columns):
        result = 0
        for column in columns:
            result += column
        result = result / len(columns)

        return result

    def normalize_values(self, norm_source, norm_dest):
        self.df[norm_dest] = (self.df[norm_source] - self.df[norm_source].min()) /\
                (self.df[norm_source].max()-self.df[norm_source].min())

    def assign_real_y(self, x, y):
        if self.df.empty is False:
            self.df.loc[self.df['best_x'] == x, 'y'] = y
            # normalize y_delta?
            self.df['y_delta'] = self.df.apply(self.calc_y_delta, axis=1)

            self.normalize_values('y', 'y_norm')

    def store_algo_choice(self, selected_algo_id, selected_algo, selected_x):
        self.selected_algo['selected_algo_id'] = selected_algo_id
        self.selected_algo['selected_algo'] = selected_algo
        self.selected_algo['selected_x'] = selected_x

    def process_model_application(self, msg):
        """
        "name": "Model_Application",
        "fields": [
            {"name": "phase", "type": ["enum"], "symbols": ["init", "observation"]},
            {"name": "model_name", "type": ["string"]},
            {"name": "id_x", "type": ["int"]},
            {"name": "n_data_points", "type": ["int"]},
            {"name": "id_start_x", "type": ["int"]},
            {"name": "model_size", "type": ["int"]},
            {"name": "best_x", "type": ["float"]},
            {"name": "best_pred_y", "type": ["float"]},
            {"name": "rmse", "type": ["null, float"]},
            {"name": "mae", "type": ["null, float"]},
            {"name": "rsquared", "type": ["null, float"]},
            {"name": "CPU_ms", "type": ["int"]},
            {"name": "RAM", "type": ["int"]}
        ]
        """
        new_model_appl = self.decode_avro_msg(msg)
        new_model_appl["timestamp"] = datetime.now()

        self.df = self.df.append(new_model_appl, ignore_index=True)
        self.normalize_values('best_pred_y', 'best_pred_y_norm')
        self.normalize_values('rmse', 'rmse_norm')
        self.normalize_values('CPU_ms', 'CPU_norm')
        self.normalize_values('RAM', 'RAM_norm')

        self.df['pred_quality'] = self.df.apply(lambda row: self.calc_avg((row['best_pred_y_norm'],
                                                                           row['rmse_norm'])), axis=1)
        self.df['resources'] = self.df.apply(lambda row: self.calc_avg((row['CPU_norm'], row['RAM_norm'])), axis=1)
        print(
            self.df[
                [
                    "model_name",
                    "best_x",
                    "best_pred_y",
                    "rmse",
                    "rsquared",
                    "CPU_ms",
                    "RAM",
                ]
            ].iloc[-1:]
        )

    def process_monitoring(self, msg):

        new_monitoring = self.decode_avro_msg(msg)

        """
        "name": "Data",
        "fields": [
            {"name": "phase", "type": ["string"]},
            {"name": "id_x", "type": ["int"]},
            {"name": "x", "type": ["float"]},
            {"name": "y", "type": ["float"]}
            ]
        """

        """
        [x] reales y muss dem Algorithmus der vorherigen Iteration zugeordnet werden


        [x] wenn Modell Wert sendet
        pred_model_quality = get_pred_model_quality(surrogate_y, new_model['rmse'])
        - normalisieren über alle Module
        - beide 1/2


        [x] wenn Monitoring reales Ergebnis sendet
        real_model_quality = get_real_model_quality(surrogate_y, real_y, rmse)
        - delta_y = | surrogate_y - real_y |
        - normalisieren
        - alle 1/3

        [x] resources = CPU, RAM
        - normalisieren
        - beide 1/2



        [x] wenn neues X ausgewählt wird
        quality = real_model_quality if real_model_quality is not None else pred_model_quality
        model = min(quality * user_spec + resources * user_spec)


        """

        # send initial design first
        if self.current_data_point < self.N_INITIAL_DESIGN:
            self.send_new_x(x=self.X[self.current_data_point])

            print(f'Sent next point from initial design x={self.X[self.current_data_point]}')

        # then send new data points
        else:
            # send last value, if no result arrived yet. necessary?
            if self.df.empty is True:
                self.send_new_x(x=new_monitoring['x'])

                print(
                    f"The value x={new_monitoring['x']} is sent to the "
                    f"CPPS, since no optimization results arrived yet."
                )

            # otherwise select and send best x
            else:
                self.assign_real_y(new_monitoring['x'], new_monitoring['y'])
                self.df['real_quality'] = self.df.apply(lambda row: self.calc_avg((row['y_norm'], row['y_delta'],
                                                                                   row['rmse_norm'])), axis=1)
                selected_algo_id, selected_algo, selected_x = self.get_best_x()

                self.store_algo_choice(selected_algo_id, selected_algo, selected_x)

                self.send_new_x(x=selected_x)

                print(f"The value x={round(selected_x, 3)} of the algorithm "
                      f"{selected_algo}({selected_algo_id}) is applied to the CPPS."
                      )

                print(self.df.loc[selected_algo_id, ["model_name",
                                                     "best_x",
                                                     "best_pred_y",
                                                     "best_pred_y_norm",
                                                     "y",
                                                     "y_norm",
                                                     "rmse",
                                                     "rmse_norm",
                                                     "pred_quality",
                                                     "real_quality",
                                                     "CPU_ms",
                                                     "RAM",
                                                     "resources"
                                                     ]
                                  ]
                      )

        self.current_data_point += 1


env_vars = {
    "config_path": os.getenv("config_path"),
    "config_section": os.getenv("config_section"),
}

"""
env_vars = {'in_topic': {'AB_model_application': './schema/model_appl.avsc',
                         'AB_monitoring': './schema/monitoring.avsc'},
            'in_group': 'evaluation',
            'in_schema_file': ['./schema/model_appl.avsc', './schema/monitoring.avsc'],
            'out_topic': 'AB_model_evaluation',
            'out_schema_file': './schema/new_x.avsc'
            }
"""

"""generate initial design"""
"""
N_INITIAL_DESIGN = 5
X_MIN = 4000.0
X_MAX = 10100.0
"""

new_cog = CognitionPC(**env_vars)

"""
"name": "New X",
"fields": [
     {"name": "new_x", "type": ["float"]}
 ]
"""

id_x = new_cog.current_data_point
x = new_cog.X[new_cog.current_data_point]

sleep(3)
new_cog.send_new_x(x=x)
new_cog.current_data_point += 1

print(
    f"Creating initial design of the system by applying {new_cog.N_INITIAL_DESIGN} "
    f"equally distributed values x over the whole working area of the CPPS."
    f"\nSent x={x}"
)

for msg in new_cog.consumer:
    new_cog.func_dict[msg.topic](msg)