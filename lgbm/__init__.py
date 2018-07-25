import os
import sys

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import roc_auc_score

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import generate_submit, load_dataset, send_line_notification
from category_encoders import TargetEncoder
from config import *
from utils import timer, timestamp

sns.set_style('darkgrid')


def run(name, feats, params, fit_params):
    with timer('load datasets'):
        X_train, y_train, X_test, cv = load_dataset(feats)
        # cv = StratifiedKFold(5, shuffle=True, random_state=71)
        print('train:', X_train.shape)
        print('test :', X_test.shape)
    
    with timer('impute missing'):
        X_train.fillna(-9999, inplace=True)
        X_test.fillna(-9999, inplace=True)
        assert X_train.isnull().sum().sum() == 0
        assert X_test.isnull().sum().sum() == 0
    
    with timer('training'):
        cv_results = []
        val_series = y_train.copy()
        test_df = pd.DataFrame()
        eval_df = pd.DataFrame(np.zeros((params['n_estimators'], cv.get_n_splits())),
                               columns=range(cv.get_n_splits()))
        feat_df = None
        for i, (trn_idx, val_idx) in enumerate(cv.split(X_train, y_train)):
            X_trn = X_train.iloc[trn_idx].copy()
            y_trn = y_train[trn_idx]
            X_val = X_train.iloc[val_idx].copy()
            y_val = y_train[val_idx]
            X_tst = X_test.copy()
            print('=' * 30, f'FOLD {i+1}/{cv.get_n_splits()}', '=' * 30)
            
            with timer('target encoding'):
                cat_cols = X_trn.select_dtypes(['object']).columns.tolist()
                te = TargetEncoder(cols=cat_cols)
                X_trn.loc[:, cat_cols] = te.fit_transform(X_trn.loc[:, cat_cols], y_trn)
                X_val.loc[:, cat_cols] = te.transform(X_val.loc[:, cat_cols])
                X_tst.loc[:, cat_cols] = te.transform(X_test.loc[:, cat_cols])
            
            with timer('fit'):
                model = lgb.LGBMClassifier(**params)
                model.fit(X_trn, y_trn, eval_set=[(X_val, y_val)], **fit_params)
            
            p = model.predict_proba(X_val)[:, 1]
            val_series.iloc[val_idx] = p
            cv_results.append(roc_auc_score(y_val, p))
            test_df[i] = model.predict_proba(X_tst)[:, 1]
            if feat_df is None:
                feat_df = pd.DataFrame(index=X_trn.columns)
            feat_df[i] = model.feature_importances_
            eval_df[i][:len(model.evals_result_['valid_0']['auc'])] = model.evals_result_['valid_0']['auc']
    
    valid_score = np.mean(cv_results)
    # valid_score = roc_auc_score(y_train, val_series.values)
    message = f"""cv: {valid_score:.5f}
scores: {[round(c, 4) for c in cv_results]}
feats: {feats}
model_params: {params}
fit_params: {fit_params}"""
    send_line_notification(message)
    print('=' * 60)
    print(message)
    print('=' * 60)
    
    with timer('output results'):
        RESULT_DIR = OUTPUT / (timestamp() + '_' + name)
        RESULT_DIR.mkdir()
        
        pd.DataFrame({'TARGET': y_train, 'p': val_series}).to_csv(RESULT_DIR / f'{name}_cv_pred.csv', index=False)
        
        pred = test_df.mean(axis=1).ravel()
        generate_submit(pred, f'{name}_{valid_score:.5f}', RESULT_DIR)
        
        print('output feature importances')
        feat_df = (feat_df / feat_df.mean(axis=0))*100
        feat_df.mean(axis=1).sort_values(ascending=False).to_csv(RESULT_DIR / 'feats.csv')
        imp = feat_df.mean(axis=1).sort_values(ascending=False)[:50]
        imp[::-1].plot.barh(figsize=(20, 15))
        plt.savefig(str(RESULT_DIR / 'feature_importances.pdf'), bbox_inches='tight')