from trainers.BaseTrainer import BaseTrainer
from tqdm import tqdm
import numpy as np
import pandas as pd
from datetime import datetime
import tensorflow as tf

from utils.metrics import AverageMeter
from utils.logger import DefinedSummarizer
import logging
import pprint


class MTrainer(BaseTrainer):
    def __init__(self, sess, model, config, logger, data_loader):
        """
        Here is the pipeline of constructing
        - Assign sess, model, config, logger, data_loader(if_specified)
        - Initialize all variables
        - Load the latest checkpoint
        - Create the summarizer
        - Get the nodes we will need to run it from the graph
        :param sess:
        :param model:
        :param config:
        :param logger:
        :param data_loader:
        """
        super(MTrainer, self).__init__(sess, model, config, logger, data_loader)

        # load the model from the latest checkpoint
        self.model.load(self.sess)

        # Summarizer
        self.summarizer = logger

        self.x, self.y, self.y_mi, self.bi, self.is_training = tf.get_collection('inputs')
        self.train_op, self.loss_node, self.acc_node = tf.get_collection('train')
        self.argmax_node = tf.get_collection('test')
        self.out_node = tf.get_collection('out')
        
        
        self.best_val_acc = 0
        self.min_val_loss = 0
        self.best_val_epoch = None
        
        self.preds = []
        self.outputs = []
        self.best_preds = []
        
        
    
    def train(self):
        """
        This is the main loop of training
        Looping on the epochs
        :return:
        """
        for cur_epoch in range(self.model.cur_epoch_tensor.eval(self.sess), self.config.num_epochs + 1, 1):
            self.train_epoch(cur_epoch)
            self.sess.run(self.model.increment_cur_epoch_tensor)
            self.test(cur_epoch)
        
        logging.info(f"Top Validaton Accuracy achieved:")
        logging.info(f"Val Epoch: {pprint.pformat(self.best_val_epoch)}")
        logging.info(f"Min Val Loss: {pprint.pformat(self.min_val_loss)}")
        logging.info(f"Best Val Accuracy: {pprint.pformat(self.best_val_acc)}")
        
        logging.info(f"Predictions: {pprint.pformat(self.best_preds)}")
        stamp = datetime.now().strftime(f"%Y-%m-%d_%H-%M-%S-")
        pd.Series(self.best_preds).to_csv('./data/val_predictions'+stamp+'.csv')
        
    def train_epoch(self, epoch=None):
        """
        Train one epoch
        :param epoch: cur epoch number
        :return:
        """
        # initialize dataset
        self.data_loader.initialize(self.sess, train=True)

        # initialize tqdm
        tt = tqdm(range(self.data_loader.num_iterations_train), total=self.data_loader.num_iterations_train,
                  desc="epoch-{}-".format(epoch))

        loss_per_epoch = AverageMeter()
        acc_per_epoch = AverageMeter()

        # Iterate over batches
        for cur_it in tt:
            # One Train step on the current batch
            loss, acc = self.train_step()
            # update metrics returned from train_step func
            loss_per_epoch.update(loss)
            acc_per_epoch.update(acc)

        self.sess.run(self.model.global_epoch_inc)
        logging.info(f"Learning rate: {pprint.pformat(self.sess.run(self.model.optimizer._lr))}")
        logging.info(f"Training Epoch: {pprint.pformat(epoch)}")
        logging.info(f"Training Loss Per Epoch: {pprint.pformat(loss_per_epoch.val)}")
        logging.info(f"Accuracy Per Epoch: {pprint.pformat(acc_per_epoch.val)}")

        
        logging.info(f"Current_si_weight: {pprint.pformat(self.sess.run(self.model.current_beta))}")
        logging.info(f"Current_mi_weight: {pprint.pformat(1 - self.sess.run(self.model.current_beta))}")
        
        # summarize
        summaries_dict = {'train/loss_per_epoch': loss_per_epoch.val,
                          'train/acc_per_epoch': acc_per_epoch.val,
                          'learning_rate' : self.sess.run(self.model.optimizer._lr),
                          'si_weight' : self.sess.run(self.model.current_beta),
                         'mi_weight' : 1 - self.sess.run(self.model.current_beta)}
        
        
        self.summarizer.summarize(self.model.global_step_tensor.eval(self.sess), summaries_dict)

        #if (self.config.save_models):
        #    self.model.save(self.sess)
        
        print("""
Epoch-{}  loss:{:.4f} -- acc:{:.4f}
        """.format(epoch, loss_per_epoch.val, acc_per_epoch.val))

        tt.close()

    def train_step(self):
        """
        Run the session of train_step in tensorflow
        also get the loss & acc of that minibatch.
        :return: (loss, acc) tuple of some metrics to be used in summaries
        """
        _, loss, acc = self.sess.run([self.train_op, self.loss_node, self.acc_node],
                                     feed_dict={self.is_training: True})
        return loss, acc
    
    def test(self, epoch):
        # initialize dataset
        self.data_loader.initialize(self.sess, train=False)

        # initialize tqdm
        tt = tqdm(range(self.data_loader.num_iterations_val), total=self.data_loader.num_iterations_val,
                  desc="Val-{}-".format(epoch))

        loss_per_epoch = AverageMeter()
        acc_per_epoch = AverageMeter()
        self.preds = []
        self.outputs = np.array([]).reshape(0, self.config.num_classes)
        
        # Iterate over batches
        for cur_it in tt:
            # One Train step on the current batch
            loss, acc, arg_max, outputs = self.sess.run([self.loss_node, self.acc_node, self.argmax_node, self.out_node],
                                     feed_dict={self.is_training: False})
            # update metrics returned from train_step func
            loss_per_epoch.update(loss)
            acc_per_epoch.update(acc)
            
            self.preds = np.append(self.preds, arg_max)
            self.outputs = np.concatenate((self.outputs, outputs[0]))
            
        
        if (self.best_val_acc < acc_per_epoch.val):
            self.best_val_acc = acc_per_epoch.val
            self.min_val_loss = loss_per_epoch.val
            self.best_val_epoch = epoch
            self.best_preds = self.preds
            
            logging.info(f"Saving Predictions to data folder.")
            logging.info(f"Saving class probabilities to data folder.")
            stamp = datetime.now().strftime(f"%Y-%m-%d_%H-%M-%S-")
            pd.Series(self.best_preds).to_csv('./data/val_predictions'+stamp+'.csv')
            pd.DataFrame(np.stack(self.outputs)).to_csv('./data/val_class_probabilities'+stamp+'.csv')
            
            if (self.config.save_models):
                self.model.save(self.sess, best = True)
            
            logging.info(f"************ NEW Validaton Accuracy achieved ************!")
            logging.info(f"Val Epoch: {pprint.pformat(epoch)}")
            logging.info(f"Min Val Loss Per Epoch: {pprint.pformat(loss_per_epoch.val)}")
            logging.info(f"Best Val Accuracy Per Epoch: {pprint.pformat(acc_per_epoch.val)}")
        
        logging.info(f"Val Epoch: {pprint.pformat(epoch)}")
        logging.info(f"Val Loss Per Epoch: {pprint.pformat(loss_per_epoch.val)}")
        logging.info(f"Val Accuracy Per Epoch: {pprint.pformat(acc_per_epoch.val)}")
        
        # summarize
        summaries_dict = {'test/loss_per_epoch': loss_per_epoch.val,
                          'test/acc_per_epoch': acc_per_epoch.val}
        self.summarizer.summarize(self.model.global_step_tensor.eval(self.sess), summaries_dict)
        
        print("""
Val-{}  loss:{:.4f} -- acc:{:.4f}
        """.format(epoch, loss_per_epoch.val, acc_per_epoch.val))

        tt.close()