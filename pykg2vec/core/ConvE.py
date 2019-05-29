#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf

import sys
sys.path.append("../")
from core.KGMeta import ModelMeta
# from pykg2vec.core.KGMeta import ModelMeta


class ConvE(ModelMeta):
    """
    ------------------Paper Title-----------------------------
    Convolutional 2D Knowledge Graph Embeddings
    ------------------Paper Authors---------------------------
    Tim Dettmers∗
    Università della Svizzera italiana
    tim.dettmers@gmail.com
    Pasquale Minervini, Pontus Stenetorp, Sebastian Riedel
    University College London
    {p.minervini,p.stenetorp,s.riedel}@cs.ucl.ac.uk
    ------------------Summary---------------------------------
    ConvE is a multi-layer convolutional network model for link prediction,
    it is a embedding model which is highly parameter efficient.
    """

    def __init__(self, config=None):
        self.config = config
        self.data_stats = self.config.kg_meta
        self.model_name = 'ConvE'
        self.dense_last_dim = {50: 2592, 100: 5184, 200: 10368}
        if self.config.hidden_size not in self.dense_last_dim:
            raise NotImplementedError("The hidden dimension is not supported!")
        self.last_dim = self.dense_last_dim[self.config.hidden_size]

    def def_inputs(self):
        self.h = tf.placeholder(tf.int32, [None])
        self.r = tf.placeholder(tf.int32, [None])
        self.t = tf.placeholder(tf.int32, [None])
        self.hr_t = tf.placeholder(tf.float32, [None, self.data_stats.tot_entity])
        self.rt_h = tf.placeholder(tf.float32, [None, self.data_stats.tot_entity])

        self.test_h_batch = tf.placeholder(tf.int32, [None])
        self.test_r_batch = tf.placeholder(tf.int32, [None])
        self.test_t_batch = tf.placeholder(tf.int32, [None])

    def def_parameters(self):
        num_total_ent = self.data_stats.tot_entity
        num_total_rel = self.data_stats.tot_relation
        k = self.config.hidden_size

        with tf.name_scope("embedding"):
            self.ent_embeddings = tf.get_variable(name="ent_embedding", shape=[num_total_ent, k],
                                                  regularizer=tf.contrib.layers.l2_regularizer(scale=0.1),
                                                  initializer=tf.contrib.layers.xavier_initializer(uniform=False))
            self.rel_embeddings = tf.get_variable(name="rel_embedding", shape=[num_total_rel, k],
                                                  regularizer=tf.contrib.layers.l2_regularizer(scale=0.1),
                                                  initializer=tf.contrib.layers.xavier_initializer(uniform=False))
        with tf.name_scope("activation_bias"):
            self.b = tf.get_variable(name="bias", shape=[self.config.batch_size, num_total_ent],
                                     initializer=tf.contrib.layers.xavier_initializer(uniform=False))
        self.parameter_list = [self.ent_embeddings, self.rel_embeddings, self.b]

    def def_layer(self):
        self.bn0 = tf.keras.layers.BatchNormalization(trainable=True)
        self.inp_drop = tf.keras.layers.Dropout(rate=self.config.input_dropout)
        self.conv2d_1 = tf.keras.layers.Conv2D(32, [3, 3], strides=(1, 1), padding='valid', activation=None,
                                               use_bias=True)
        self.bn1 = tf.keras.layers.BatchNormalization(trainable=True)
        self.feat_drop = tf.keras.layers.Dropout(rate=self.config.feature_map_dropout)
        self.fc1 = tf.keras.layers.Dense(units=self.config.hidden_size)
        self.hidden_drop = tf.keras.layers.Dropout(rate=self.config.hidden_dropout)
        self.bn2 = tf.keras.layers.BatchNormalization(trainable=True)

    def forward(self, st_inp):
        # batch normalization in the first axis
        x = self.bn0(st_inp)
        # input dropout
        x = self.inp_drop(x)
        # 2d convolution layer, output channel =32, kernel size = 3,3
        x = self.conv2d_1(x)
        # batch normalization across feature dimension
        x = self.bn1(x)
        # first non-linear activation
        x = tf.nn.relu(x)
        # feature dropout
        x = self.feat_drop(x)
        # reshape the tensor to get the batch size
        '''10368 with k=200,5184 with k=100, 2592 with k=50'''
        x = tf.reshape(x, [-1, self.last_dim])
        # pass the feature through fully connected layer, output size = batch size, hidden size
        x = self.fc1(x)
        # dropout in the hidden layer
        x = self.hidden_drop(x)
        # batch normalization across feature dimension
        x = self.bn2(x)
        # second non-linear activation
        x = tf.nn.relu(x)
        # project and get inner product with the tail triple
        x = tf.matmul(x, tf.transpose(tf.nn.l2_normalize(self.ent_embeddings, axis=1)))
        # add a bias value
        # x = tf.add(x, self.b)
        # sigmoid activation
        return tf.nn.sigmoid(x)

    def def_loss(self):
        ent_emb_norm = tf.nn.l2_normalize(self.ent_embeddings, axis=1)
        rel_emb_norm = tf.nn.l2_normalize(self.rel_embeddings, axis=1)

        h_emb = tf.nn.embedding_lookup(ent_emb_norm, self.h)
        r_emb = tf.nn.embedding_lookup(rel_emb_norm, self.r)
        t_emb = tf.nn.embedding_lookup(ent_emb_norm, self.t)

        hr_t = self.hr_t * (1.0 - self.config.label_smoothing) + 1.0 / self.data_stats.tot_entity
        rt_h = self.rt_h * (1.0 - self.config.label_smoothing) + 1.0 / self.data_stats.tot_entity

        stacked_h = tf.reshape(h_emb, [-1, 10, 20, 1])
        stacked_r = tf.reshape(r_emb, [-1, 10, 20, 1])
        stacked_t = tf.reshape(t_emb, [-1, 10, 20, 1])

        stacked_hr = tf.concat([stacked_h, stacked_r], 1)
        stacked_tr = tf.concat([stacked_t, stacked_r], 1)

        # TODO make two different forward layers for head and tail
        pred_tails = self.forward(stacked_hr)
        pred_heads = self.forward(stacked_tr)

        loss_tail_pred = tf.reduce_mean(tf.keras.backend.binary_crossentropy(hr_t, pred_tails))
        loss_head_pred = tf.reduce_mean(tf.keras.backend.binary_crossentropy(rt_h, pred_heads))

        reg_losses = tf.nn.l2_loss(h_emb) + tf.nn.l2_loss(r_emb) + tf.nn.l2_loss(t_emb)

        self.loss = loss_tail_pred + loss_head_pred + self.config.lmbda * reg_losses

    def test_batch(self):
        ent_emb_norm = tf.nn.l2_normalize(self.ent_embeddings, axis=1)
        rel_emb_norm = tf.nn.l2_normalize(self.rel_embeddings, axis=1)

        h_emb = tf.nn.embedding_lookup(ent_emb_norm, self.test_h_batch)
        r_emb = tf.nn.embedding_lookup(rel_emb_norm, self.test_r_batch)
        t_emb = tf.nn.embedding_lookup(ent_emb_norm, self.test_t_batch)

        stacked_h = tf.reshape(h_emb, [-1, 10, 20, 1])
        stacked_r = tf.reshape(r_emb, [-1, 10, 20, 1])
        stacked_t = tf.reshape(t_emb, [-1, 10, 20, 1])

        stacked_hr = tf.concat([stacked_h, stacked_r], 1)
        stacked_tr = tf.concat([stacked_t, stacked_r], 1)

        # TODO make two different forward layers for head and tail
        pred_tails = self.forward(stacked_hr)
        pred_heads = self.forward(stacked_tr)

        _, head_rank = tf.nn.top_k(pred_heads, k=self.data_stats.tot_entity)
        _, tail_rank = tf.nn.top_k(pred_tails, k=self.data_stats.tot_entity)

        return head_rank, tail_rank

    def embed(self, h, r, t):
        """function to get the embedding value"""
        emb_h = tf.nn.embedding_lookup(self.ent_embeddings, h)
        emb_r = tf.nn.embedding_lookup(self.rel_embeddings, r)
        emb_t = tf.nn.embedding_lookup(self.ent_embeddings, t)
        return emb_h, emb_r, emb_t

    def get_embed(self, h, r, t, sess=None):
        """function to get the embedding value in numpy"""
        emb_h, emb_r, emb_t = self.embed(h, r, t)
        h, r, t = sess.run([emb_h, emb_r, emb_t])
        return h, r, t

    def get_proj_embed(self, h, r, t, sess):
        """function to get the projected embedding value in numpy"""
        return self.get_embed(h, r, t, sess)

