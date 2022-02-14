import math
import torch
from .dataselectionstrategy import DataSelectionStrategy
from cords.utils.data.datasets.SL.builder import CustomDataset
from torch.utils.data import  DataLoader
import numpy as np


class SELCONstrategy(DataSelectionStrategy):
    def __init__(self, trainloader, valloader, model, 
                loss_func, device, num_classes, delta, 
                linear_layer, lam, lr, logger, optimizer, 
                batch_size, criterion):
        """
        Constructor method
        """
        super().__init__(trainloader, valloader, model, num_classes, linear_layer, loss_func, device, logger)
        self.delta = delta
        self.lam = lam
        self.lr = lr
        self.optimizer = optimizer
        self.batch_size = batch_size
        self.criterion = criterion
        # self.sub_epoch sub_epoch
        self.x_trn, self.y_trn, self.x_val, self.y_val = self.get_train_val()
        self.__precompute()     # doubt: no other algo (in cords) do a precomputation

    def get_train_val(self):
        # doubt: To check
        x_trn = self.trainloader.dataset.data
        y_trn = self.trainloader.dataset.labels
        x_val = self.valloader.dataset.data
        y_val = self.valloader.dataset.labels
        return x_trn, y_trn, x_val, y_val

    def __precompute(self, f_pi_epoch, p_epoch, alphas): # TODO: alphas?
        main_optimizer = torch.optim.Adam([
                {'params': self.model.parameters()}], lr=self.lr)
                
        dual_optimizer = torch.optim.Adam([{'params': alphas}], lr=self.lr)

        print("SELCON: starting pre compute")

        loader_val = DataLoader(CustomDataset(self.x_val, self.y_val,transform=None),\
            shuffle=False,batch_size=self.batch_size)
        # loader_val = self.valloader # todo

        prev_loss = 1000
        stop_count = 0
        i = 0

        while(True):
            main_optimizer.zero_grad()
            constraint = 0.

            for batch_idx in list(loader_val.batch_sampler):
                inputs, targets = loader_val.dataset[batch_idx]
                inputs, targets = inputs.to(self.device), targets.to(self.device)
                val_out = self.model(inputs)
                constraint += self.criterion(val_out, targets)
            
            constraint /= len(loader_val.batch_sampler)
            constraint = constraint - self.delta
            multiplier = alphas * constraint # todo: try torch.dot(alphas, constraint)

            loss = multiplier
            self.F_phi = loss.item()
            loss.backward()
            main_optimizer.step()

            dual_optimizer.zero_grad()
            constraint = 0.

            for batch_idx in list(loader_val.batch_sampler):
                inputs, targets = loader_val.dataset[batch_idx]
                inputs, targets = inputs.to(self.device), targets.to(self.device)
                
                val_out = self.model(input)
                constraint += self.criterion(val_out, targets)
            
            constraint /= len(loader_val.batch_sampler)
            constraint = constraint - self.delta
            multiplier = -1. * alphas * constraint # todo: try -1.*torch.dot(alphas, constraint)

            multiplier.backward()
            dual_optimizer.step()

            alphas.requires_grad = False
            alphas.clamp_(min=0.)
            alphas.requires_grad = True

            if loss.item() <= 0.:
                break

            if prev_loss - loss.item() < 1e-3 and stop_count >= 5:
                if stop_count >= 5:
                    break
                else:
                    stop_count += 1
            else:
                stop_count = 0
            
            prev_loss = loss.item()
            i += 1
        
        print("SELCON: Finishing F phi")

        if loss.item() <= 0.:
            alphas = torch.zeros_like(alphas)
        
        l = [torch.flatten(p) for p in self.model.state_dict().values()]
        flat = torch.cat(l).detach().clone()

        self.F_values = torch.zeros(len(self.x_trn), device=self.device)

        beta1, beta2 = main_optimizer.param_groups[0]['betas']

        loader_tr = DataLoader(CustomDataset(self.x_trn, self.y_trn,\
            transform=None, return_ids=True), device = self.device, shuffle=False,batch_size=self.batch_size*20)

        loader_val = DataLoader(CustomDataset(self.x_val, self.y_val,device = self.device,transform=None),\
            shuffle=False,batch_size=self.batch_size*20)    

        for batch_idx in list(loader_tr.batch_sampler):
            inputs, targets, idxs = loader_tr.dataset[batch_idx]
            inputs, targets = inputs.to(self.device), targets.to(self.device)

            ele_delta = self.delta.repeat(targets.shape[0]).to(self.device)

            weights = flat.view(1, -1).repeat(targets.shape[0], 1)
            ele_alphas = alphas.detach().repeat(targets.shape[0]).to(self.device)

            exp_avg_w = torch.zeros_like(weights)
            exp_avg_sq_w = torch.zeros_like(weights)

            exten_inp = torch.cat((inputs, torch.ones(inputs.shape[0], \
                device=self.device).view(-1,1)), dim=1)

            bias_correction1 = 1.
            bias_correction2 = 1.

            for i in range(p_epoch):
                trn_loss_g = torch.sum(exten_inp*weights, dim=1) - targets
                fin_trn_loss_g = 2 * exten_inp * trn_loss_g[:, None]

                weight_grad = fin_trn_loss_g +2*self.lam * torch.cat((weights[:,:-1],\
                            torch.zeros((weights.shape[0],1),device=self.device)),dim=1)
                    
                exp_avg_w.mul_(beta1).add_(1. - beta1, weight_grad)
                exp_avg_sq_w.mul_(beta2).addcmul_(1. - beta2, weight_grad, weight_grad)
                denom = exp_avg_sq_w.sqrt().add_(main_optimizer.param_groups[0]['eps'])

                bias_correction1 *= beta1
                bias_correction2 *= beta2
                step_size = self.lr * math.sqrt(1. - bias_correction2) / (1. - bias_correction1) # doubt: why sqrt only on numerator?
                weights.addcdiv_(-step_size, exp_avg_w, denom)
            
            val_losses = 0.
            for batch_idx_val in list(loader_val.batch_sampler):
                # doubt: what's going on here?
                inputs, targets = loader_val.dataset[batch_idx_val]
                inputs, targets = inputs.to(self.device), targets.to(self.device)

                exten_val = torch.cat((inputs, torch.ones(inputs.shape[0], device=self.device).view(-1,1)), dim=1)
                exten_val_y = torch.mean(targets).repeat(min(self.batch_size*20, targets.shape[0]))

                val_loss = torch.sum(weights*torch.mean(exten_val,dim=0),dim=1) - exten_val_y

                val_losses+= val_loss*val_loss #torch.mean(val_loss*val_loss,dim=0)

            reg = torch.sum(weights[:,:-1]*weights[:,:-1], dim=1)
            trn_loss = torch.sum(exten_inp*weights, dim=1) - targets

            self.F_values[idxs] = trn_loss*trn_loss+ self.lam*reg +torch.max(torch.zeros_like(ele_alphas),\
                (val_losses/len(loader_val.batch_sampler)-ele_delta)*ele_alphas)

            print("SELCON: Finishing element wise F")

    def __return_subset(self, theta_init, p_epoch, curr_subset, budget, 
                        batch, step, w_exp_avg, w_exp_avg_sq):

        m_values = self.F_values.detach().clone()
        self.model.load_state_dict(theta_init) # todo: use this, update theta_init before calling this function

        loader_tr = DataLoader(CustomDataset(self.x_trn[curr_subset], self.y_trn[curr_subset],\
            transform=None, return_ids=True),shuffle=False,batch_size=batch)

        sum_error = torch.nn.MSELoss(reduction='sum') # doubt: why not use self.criterion here, also check the reduction here and nored

        with torch.no_grad():
            F_curr = 0.
            for batch_idx in list(loader_tr.batch_sampler):

                inputs, targets, _ = loader_tr.dataset[batch_idx]
                inputs, targets = inputs.to(self.device), targets.to(self.device)

                scores = self.model(inputs)
                F_curr += sum_error(scores, targets).item() 

            l = [torch.flatten(p) for p in self.model.parameters()]
            flatt = torch.cat(l)
            l2_reg = torch.sum(flatt[:-1]*flatt[:-1])

            F_curr += (self.lam*l2_reg*len(curr_subset)).item() #+ multiplier).item()

        main_optimizer = torch.optim.Adam([{'params': self.model.parameters()}], lr=self.lr)

        l = [torch.flatten(p) for p in self.model.state_dict().values()]
        flat = torch.cat(l).detach()

        loader_tr = DataLoader(CustomDataset(self.x_trn[curr_subset], self.y_trn[curr_subset],\
            transform=None, return_ids=True),shuffle=False,batch_size=self.batch_size)

        beta1,beta2 = main_optimizer.param_groups[0]['betas']
        rem_len = (len(curr_subset)-1)
        b_idxs = 0
        device_new = self.device


        for batch_idx in list(loader_tr.batch_sampler):

            inputs, targets, _ = loader_tr.dataset[batch_idx]
            inputs, targets = inputs.to(self.device), targets.to(self.device)
        
            weights = flat.repeat(targets.shape[0], 1)

            exp_avg_w = w_exp_avg.repeat(targets.shape[0], 1)
            exp_avg_sq_w = w_exp_avg_sq.repeat(targets.shape[0], 1)

            exten_inp = torch.cat((inputs,torch.ones(inputs.shape[0],device=self.device).view(-1,1)),dim=1)

            bias_correction1 = beta1**step#1.0 
            bias_correction2 = beta2**step#1.0 

            for i in range(p_epoch):

                sum_fin_trn_loss_g = torch.zeros_like(weights).to(device_new)
                for batch_idx_trn in list(loader_tr.batch_sampler):
                    
                    inputs_trn, targets_trn,_ = loader_tr.dataset[batch_idx_trn]
                    inputs_trn, targets_trn = inputs_trn.to(self.device), targets_trn.to(self.device)

                    exten_trn = torch.cat((inputs_trn,torch.ones(inputs_trn.shape[0]\
                        ,device=self.device).view(-1,1)),dim=1).to(device_new)
                    exten_trn_y = targets_trn.view(-1,1).repeat(1,min(self.batch_size,\
                        targets.shape[0])).to(device_new)
                
                    sum_trn_loss_p = 2*(torch.matmul(exten_trn,torch.transpose(weights, 0, 1)\
                        .to(device_new)) - exten_trn_y)
            
                    sum_fin_trn_loss_g += torch.sum(sum_trn_loss_p[:,:,None]*exten_trn[:,None,:],dim=0)

                    del exten_trn,exten_trn_y,sum_trn_loss_p,inputs_trn, targets_trn #mod_trn,sum_trn_loss_g,
                    torch.cuda.empty_cache()

                sum_fin_trn_loss_g = sum_fin_trn_loss_g.to(self.device)

                trn_loss_g = torch.sum(exten_inp*weights,dim=1) - targets
                fin_trn_loss_g = exten_inp*2*trn_loss_g[:,None]

                fin_trn_loss_g = (sum_fin_trn_loss_g - fin_trn_loss_g)/rem_len

                weight_grad = fin_trn_loss_g+ 2*rem_len*\
                    torch.cat((weights[:,:-1], torch.zeros((weights.shape[0],1),device=self.device)),dim=1)#+\

                exp_avg_w.mul_(beta1).add_(1.0 - beta1, weight_grad)
                exp_avg_sq_w.mul_(beta2).addcmul_(1.0 - beta2, weight_grad, weight_grad)
                denom = exp_avg_sq_w.sqrt().add_(main_optimizer.param_groups[0]['eps'])

                bias_correction1 *= beta1
                bias_correction2 *= beta2
                step_size = (self.lr)* math.sqrt(1.0-bias_correction2) / (1.0-bias_correction1)
                weights.addcdiv_(-step_size, exp_avg_w, denom)
            
            reg = torch.sum(weights[:,:-1]*weights[:,:-1],dim=1)

            trn_losses = 0.
            for batch_idx_trn in list(loader_tr.batch_sampler):
                    
                inputs_trn, targets_trn,_ = loader_tr.dataset[batch_idx_trn]
                inputs_trn, targets_trn = inputs_trn.to(self.device), targets_trn.to(self.device)

                exten_trn = torch.cat((inputs_trn,torch.ones(inputs_trn.shape[0],device=self.device).view(-1,1)),dim=1)
                exten_trn_y = targets_trn.view(-1,1).repeat(1,min(self.batch_size,targets.shape[0]))
            
                trn_loss = torch.matmul(exten_trn,torch.transpose(weights, 0, 1)) - exten_trn_y
                
                trn_losses+= torch.sum(trn_loss*trn_loss,dim=0)

            trn_loss_ind = torch.sum(exten_inp*weights,dim=1) - targets
            trn_losses -= trn_loss_ind*trn_loss_ind
            abs_value = F_curr - (trn_losses + self.lam*reg*rem_len) #\
            neg_ind = ((abs_value ) < 0).nonzero().view(-1)
            abs_value [neg_ind] = torch.max(self.F_values)
            m_values[torch.tensor(curr_subset)[b_idxs*self.batch_size:(b_idxs+1)*self.batch_size]] = abs_value
            b_idxs +=1

        values,indices =m_values.topk(budget,largest=False)

        return list(indices.cpu().numpy()), list(values.cpu().numpy())

    def select(self, budget, model_params):
        N, _ = self.x_trn.shape
        current_idx = list(np.random.choice(N, budget, replace=False)) # take this from prev train loop
        state_values = list(self.optimizer.state.values())
        step = state_values[0]['step']
        w_exp_avg = torch.cat((state_values[0]['exp_avg'].view(-1),state_values[1]['exp_avg']))
        w_exp_avg_sq = torch.cat((state_values[0]['exp_avg_sq'].view(-1),state_values[1]['exp_avg_sq']))
        sub_epoch = 3

        # doubt: where to get batch_size and sub_epoch from?
        return self.__return_subset(
            theta_init=model_params,
            p_epoch=sub_epoch,
            curr_subset=current_idx,
            budget=budget,
            batch=self.batch_size, # assert this is train batch size
            step=step,
            w_exp_avg=w_exp_avg,
            w_exp_avg_sq=w_exp_avg_sq
        )
        