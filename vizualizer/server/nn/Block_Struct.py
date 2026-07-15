import os
import tensorflow as tf
import re
#src.NeuralTools.custom_layers import Pad2D
import uuid
from copy import copy


class BlockStruct:
    block_params = {'c': {'name': 'Conv2D',  # Сверточный слой
                          'func': tf.keras.layers.Conv2D,
                          'params': {'filters': 64,
                                     'kernel_size': (3, 3),
                                     'padding': 'same',
                                     'activation': 'relu',
                                     'data_format': None,
                                     'dilation_rate': (1, 1),
                                     'groups': 1,
                                     'use_bias': True,
                                     'kernel_initializer': 'glorot_uniform',
                                     'bias_initializer': 'zeros',
                                     'kernel_regularizer': None,
                                     'bias_regularizer': None,
                                     'activity_regularizer': None,
                                     'kernel_constraint': None,
                                     'bias_constraint': None}},
                    'sc': {'name': 'Strides_Conv2D',  # Сверточный слой со страйдами
                           'func': tf.keras.layers.Conv2D,
                           'params': {'filters': 64,
                                      'kernel_size': (3, 3),
                                      'strides': (2, 2),
                                      'padding': 'same',
                                      'activation': 'relu',
                                      'data_format': None,
                                      'dilation_rate': (1, 1),
                                      'groups': 1,
                                      'use_bias': True,
                                      'kernel_initializer': 'glorot_uniform',
                                      'bias_initializer': 'zeros',
                                      'kernel_regularizer': None,
                                      'bias_regularizer': None,
                                      'activity_regularizer': None,
                                      'kernel_constraint': None,
                                      'bias_constraint': None}},
                    'mp': {'name': 'Max_pooling',  # MaxPooling слой
                           'func': tf.keras.layers.MaxPool2D,
                           'params': {'pool_size': (2, 2),
                                      'padding': 'valid',
                                      'data_format': None}},
                    'ct': {'name': 'Conv2D_Transpose',  # Транспонированная конволюция со страйдами
                           'func': tf.keras.layers.Conv2DTranspose,
                           'params': {'filters': 64,
                                      'kernel_size': (3, 3),
                                      'strides': (2, 2),
                                      'activation': 'relu',
                                      'padding': 'same',
                                      'output_padding': None,
                                      'data_format': None,
                                      'dilation_rate': (1, 1),
                                      'use_bias': True,
                                      'kernel_initializer': 'glorot_uniform',
                                      'bias_initializer': 'zeros',
                                      'kernel_regularizer': None,
                                      'bias_regularizer': None,
                                      'activity_regularizer': None,
                                      'kernel_constraint': None,
                                      'bias_constraint': None}},
                    'us': {'name': 'UpSampling',  # UpSampling слой
                           'func': tf.keras.layers.UpSampling2D,
                           'params': {'size': (2, 2),
                                      'data_format': None,
                                      'interpolation': 'nearest'}},
                    'bn': {'name': 'BatchNormalization',  # Слой нормализации данных из батча
                           'func': tf.keras.layers.BatchNormalization,
                           'params': {'axis': -1,
                                      'momentum': 0.99,
                                      'epsilon': 0.001,
                                      'center': True,
                                      'scale': True,
                                      'beta_initializer': 'zeros',
                                      'gamma_initializer': 'ones',
                                      'moving_mean_initializer': 'zeros',
                                      'moving_variance_initializer': 'ones',
                                      'beta_regularizer': None,
                                      'gamma_regularizer': None,
                                      'beta_constraint': None,
                                      'gamma_constraint': None}},
                    'den': {'name': 'Dense',
                            'func': tf.keras.layers.Dense,
                            'params': {'units': 1,
                                       'activation': None,
                                       'use_bias': True,
                                       'kernel_initializer': 'glorot_uniform',
                                       'bias_initializer': 'zeros',
                                       'kernel_regularizer': None,
                                       'bias_regularizer': None,
                                       'activity_regularizer': None,
                                       'kernel_constraint': None,
                                       'bias_constraint': None, }},
                    'drop': {'name': 'Dropout',
                             'func': tf.keras.layers.Dropout,
                             'params': {'rate': 0.1,
                                        'noise_shape': None,
                                        'seed': None}},
                    're': {'name': 'Reshape',
                    	   'func': tf.keras.layers.Reshape,
                    	   'params': {'target_shape': (-1, 1, 1)}},
                    'fl': {'name': 'Flatten',
                           'func': tf.keras.layers.Flatten,
                           'params': {'data_format': None}},
                    'act': {'name': 'Activation',  # Слой активации
                            'func': tf.keras.layers.Activation,
                            'params': {'activation': tf.nn.relu}},
                    'add': {'name': 'AddLayer',  # Слой объединения тензоров
                            'func': tf.keras.layers.Add,
                            'params': dict()},
                    'out': {'name': 'OutTensor',
                            # Служебный элемент, указывающий на выход из модели в промежуточном звене
                            'func': None,
                            'params': dict()}
                    }

    def __init__(self, block_name: str, structure: str, *args, **kwargs):
        """
        :param block_name: Имя блока нейронной сети, которое будет присваиваться к названиям слоев
        :param structure: Стока, содержащая структуру нейронной сети. Правила заполнения: слои отделены знаком "_".
                 Тип слоя указывается в соответствии с условным обозначением из block_params,
                 перед названием может быть указано число, определяющее количество следующих друг
                 за другом слоев этого типа, кроме того в строку структуры может быть указан отдельный BlockStruct,
                 описывающий блок из слоев, для этого можно в {} либо указать индекс из входных параметров *args,
                 либо в {} задать имя с названием из **kwargs. Если необходимо указать вход в сеть тензора из
                 skipped connections (есть соответствующий out в сети), необходимо указать add{N}, где N - порядковый
                 индекс out из начала сети.
                 Примеры структуры сети:
                 '3c_sc_3c_us_4c' - сверточная сеть: 3 слоя Conv2D -> Conv2D со страйдами -> 3 слоя Conv2d -> UpSampling -> 4 слоя Conv2D
                 '3c_out_sc_3c_us_add{0}_4c' - сверточная сеть со skipped connections
                 '3c_{0}_sc_3c' - сверточная сеть в которой вместо {0} будет использован класс Block_struct, указанный в *args первым элементом
                 '3c_{new_block}_sc_3c' - сверточная сеть, в которой используется класс Block_struct, который присвоен параметру new_block в **kwargs
        :param start_idx: с какого значения начинается нумераяция слоев, по умолчанию - 0
        :param options: Параметры слоев, задается в виде словаря, где ключ - индекс группы слоев (начиная с 0) в строке structure
                        следует задавать только те параметры, которые отличаются от принятых по умолчанию
        :param args: Могут быть безыменнованные параметры в качестве блоков сетей в виде класса BlockStruct
        :param kwargs: Именнованные параметры, в которых могут быть указаны параметры с объектами типа BlockStruct
                       Кроме того, отдельно задается параметр options
        """
        self.__naming = block_name
        self.__init_params(**kwargs)
        self.__reg_nested_structures(*args, **kwargs)
        self.__build_layers(structure)

    def __init_params(self, **kwargs):
        self.__start_idx = kwargs.pop('start_idx', 0)
        self.__start_offset = kwargs.pop('start_offset', 0)
        self.__options = kwargs.pop('options', dict())
        self.__layers_num = 0
        self.__layers_chain = []
        self.__reversed = False
        self.__iter_step = 1

    def __reg_nested_structures(self, *args, **kwargs):
        self.__nested_structures = dict()
        if args:
            for idx, arg in enumerate(args):
                self.__nested_structures[idx] = arg
        if kwargs:
            for block_name in kwargs.keys():
                self.__nested_structures[block_name] = kwargs[block_name]

    class LayerSpec:
        def __init__(self, naming, idx, properties, **kwargs):
            name = f'{idx}_{naming}_{properties["name"]}'
            self.__id = uuid.uuid4()
            self.__vals = kwargs.pop('values', [])
            if self.__vals is None:
                self.__vals = []
            self.__params = dict(properties['params'])
            self.__func = properties['func']
            for key in kwargs.keys():
                if key in self.__params:
                    self.__params[key] = kwargs[key]
            self.__params['name'] = name

        def __call__(self):
            if self.__func is not None:
                res = self.__func(**self.__params)
                if not self.__vals:
                    return self.l_id, res
                elif self.is_type('AddLayer'):
                    return self.l_id, 'add', res, self.__vals
                else:
                    raise ValueError(f'В слое {self.__params["name"]} неверно заданы параметры')
            else:
                if self.is_type('OutTensor'):
                    return self.l_id, 'out'

        @property
        def l_id(self):
            return self.__id

        @property
        def name(self):
            return self.__params['name']

        @property
        def values(self):
            return self.__vals

        def set_values(self, values):
            self.__vals = values

        def is_type(self, l_type):
            if l_type in self.__params['name']:
                return True
            else:
                return False

        def update(self, idx, naming='', level=(0, 0, None)):
            new_name = self.__params['name'][self.__params['name'].find('_') + 1:]
            if naming != '':
                name = f'{idx}_{naming}_{new_name[new_name.find("_") + 1:]}'
            else:
                name = '_'.join([str(idx), new_name])
            self.__params['name'] = name
            self.__level = level
            old_id = copy(self.__id)
            self.__id = uuid.uuid4()
            return old_id

    def __read_group(self, group: str):
        res = dict(num=0, name='', BlockStruct=False, vals=None)
        if group[0].isdigit():
            layers_num = re.search(r'\d+', group).group()
            res['name'] = group[len(layers_num):]
            res['num'] = int(layers_num)
        else:
            res['name'] = group
            res['num'] = 1
        bracket = res['name'].find('{')
        if bracket == 0 and res['name'][-1] == '}':
            res['name'] = res['name'][1:-1]
            if res['name'].isdigit():
                res['name'] = int(res['name'])
            res['BlockStruct'] = True
        elif bracket > -1 and res['name'][-1] == '}':
            if res['name'][0: bracket] == 'add':
                res['vals'] = [int(val) for val in res['name'][bracket + 1: -1].split(',')]
                res['name'] = 'add'
            else:
                raise ValueError(f'Неправильно задана структура слоев, ошибка в скобках: {res["name"]}')
        else:
            pass
        return res

    def update(self, naming='', start_idx=0):
        if naming != '':
            self.__naming = naming
        self.__start_idx = start_idx
        idx = self.__start_idx
        outlets = dict()
        for layer in self.__layers_chain:
            if isinstance(layer, self.LayerSpec):
                old_id = layer.update(idx, naming)
                if layer.is_type('OutTensor'):
                    outlets[old_id] = layer.l_id
                elif layer.is_type('AddLayer'):
                    new_con = [outlets[oldid] for oldid in layer.values]
                    layer.set_values(new_con)
                else:
                    pass
                idx += 1
            elif isinstance(layer, dict):
                bl_struct = self.__nested_structures[layer['block_name']]
                bl_struct.update(naming, start_idx=idx)
                layer['start_idx'] = idx
                idx += len(bl_struct)
        #self.__layers_num = idx - self.__start_idx

    def __build_layers(self, structure: str):
        idx = self.__start_idx
        inlets = []
        outlets = []
        for group_idx, group in enumerate(structure.split('_')[self.__start_offset:]) :
            group_data = self.__read_group(group)
            if not group_data['BlockStruct']:
                layer_def_opts = self.block_params[group_data['name']]
                layer_opts = self.__options.get(group_idx, dict())
                for _ in range(group_data['num']):
                    layer = self.LayerSpec(self.__naming, idx, layer_def_opts, values=group_data['vals'], **layer_opts)
                    idx += 1
                    if layer.is_type('AddLayer'):
                        inlets.append(layer)
                    elif layer.is_type('OutTensor'):
                        outlets.append(layer.l_id)
                    self.__layers_chain.append(layer)
            else:
                for _ in range(group_data['num']):
                    self.__layers_chain.append(dict(block_name=group_data['name'], start_idx=idx))
                    idx += len(self.__nested_structures[group_data['name']])
        for layer in inlets:
            new_con = [outlets[i] for i in layer.values]
            layer.set_values(new_con)
        self.__layers_num = idx - self.__start_idx

    def __getitem__(self, idx):
        return self.__layers_chain[idx]

    def __len__(self):
        return self.__layers_num

    def __iter__(self):
        self.__c_idx = 0
        self.__c_l = self.__start_offset
        return self

    def __next__(self):
        if self.__c_l == len(self.__layers_chain):
            raise StopIteration
        layer = self.__layers_chain[self.__c_l]
        if isinstance(layer, dict):
            block = self.__nested_structures[layer['block_name']]
            #block.update('', self.__c_idx)
            self.__c_l += 1
            #self.__c_idx += len(block)
            return block(naming='', start_idx=layer['start_idx'])
        else:
            self.__c_l += 1
            #self.__c_idx += 1
            return [layer()]


    @property
    def chain(self):
        return self.__layers_chain.copy()

    def __call__(self, naming='', start_idx=-1):
        if start_idx != -1 or naming != '':
            self.update(naming, start_idx)
        res = []
        for layer in self:
            if not isinstance(layer, list):
                res.append(layer)
            else:
                res.extend(layer)
        return res
#
def create_neural_model(block: BlockStruct, input_layer):
    temp = []
    outlets = dict()
    out = input_layer
    prev_layer_data = dict(id=-1, tensor=-1)
    for layer_gr in block:
        for layer in layer_gr:
            temp.append(layer)
            if len(layer) == 4:
                if layer[1] == 'add':
                    add = [out]
                    add.extend([outlets[layer_id] for layer_id in layer[3]])
                    out = layer[2](add)
                    update = True
                else:
                    update = False
            elif len(layer) == 2:
                if isinstance(layer[1], str):
                    if layer[1] == 'out':
                        outlets[layer[0]] = prev_layer_data['tensor']
                        update = False
                    else:
                        update = False
                else:
                    out = layer[1](out)
                    update = True
            else:
                update = False
            if update:
                prev_layer_data['id'] = layer[0]
                prev_layer_data['tensor'] = out
    return tf.keras.Model(inputs=input_layer, outputs=[out])
