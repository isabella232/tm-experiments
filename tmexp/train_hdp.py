from argparse import ArgumentParser
import logging
import os
from typing import Dict, List, Tuple

import gensim
import numpy as np
import tqdm

from .cli import CLIBuilder, register_command
from .io_constants import (
    BOW_DIR,
    DOCTOPIC_FILENAME,
    DOCWORD_CONCAT_FILENAME,
    DOCWORD_FILENAME,
    TOPICS_DIR,
    VOCAB_FILENAME,
    WORDTOPIC_FILENAME,
)
from .utils import check_file_exists, check_remove, create_directory, create_logger


def _define_parser(parser: ArgumentParser) -> None:
    cli_builder = CLIBuilder(parser)
    cli_builder.add_bow_arg(required=True)
    cli_builder.add_experiment_arg(required=False)
    cli_builder.add_force_arg()
    cli_builder.add_consolidate_arg()

    parser.add_argument(
        "--chunk-size", help="Number of documents in one chunk.", default=256, type=int
    )
    parser.add_argument(
        "--kappa",
        help="Learning parameter which acts as exponential decay factor to influence "
        "extent of learning from each batch.",
        default=1.0,
        type=float,
    )
    parser.add_argument(
        "--tau",
        help="Learning parameter which down-weights early iterations of documents.",
        default=64.0,
        type=float,
    )
    parser.add_argument(
        "--K", help="Document level truncation level.", default=15, type=int
    )
    parser.add_argument(
        "--T", help="Topic level truncation level.", default=150, type=int
    )
    parser.add_argument(
        "--alpha", help="Document level concentration.", default=1, type=int
    )
    parser.add_argument(
        "--gamma", help="Topic level concentration.", default=1, type=int
    )
    parser.add_argument("--eta", help="Topic Dirichlet.", default=0.01, type=float)
    parser.add_argument(
        "--scale",
        help="Weights information from the mini-chunk of corpus to calculate rhot.",
        default=1.0,
        type=float,
    )
    parser.add_argument(
        "--var-converge",
        help="Lower bound on the right side of convergence.",
        default=0.0001,
        type=float,
    )


GensimCorpus = List[List[Tuple[int, int]]]


def create_gensim_corpus(input_path: str, logger: logging.Logger) -> GensimCorpus:
    with open(input_path, "r", encoding="utf-8") as fin:
        corpus: GensimCorpus = [[] for _ in range(int(fin.readline()))]
        logger.info("\tNumber of documents: %d", len(corpus))
        num_words = int(fin.readline())
        logger.info("\tNumber of words: %d", num_words)
        num_rows = int(fin.readline())
        logger.info("\tNumber of document/word pairs: %d", num_rows)
        for line in tqdm.tqdm(fin, total=num_rows):
            doc_id, word_id, count = map(int, line.split())
            corpus[doc_id].append((word_id - 1, count))
    return corpus


@register_command(parser_definer=_define_parser)
def train_hdp(
    bow_name: str,
    exp_name: str,
    force: bool,
    chunk_size: int,
    kappa: float,
    tau: float,
    K: int,
    T: int,
    alpha: int,
    gamma: int,
    eta: float,
    scale: float,
    var_converge: float,
    consolidate: bool,
    log_level: str,
) -> None:
    """Train an HDP model from the input BoW."""
    logger = create_logger(log_level, __name__)

    input_dir = os.path.join(BOW_DIR, bow_name)
    words_input_path = os.path.join(input_dir, VOCAB_FILENAME)
    check_file_exists(words_input_path)
    docword_input_path = os.path.join(input_dir, DOCWORD_FILENAME)
    check_file_exists(docword_input_path)
    if consolidate:
        docword_concat_input_path = os.path.join(input_dir, DOCWORD_CONCAT_FILENAME)
        check_file_exists(docword_concat_input_path)

    output_dir = os.path.join(TOPICS_DIR, bow_name, exp_name)
    doctopic_output_path = os.path.join(output_dir, DOCTOPIC_FILENAME)
    check_remove(doctopic_output_path, logger, force)
    wordtopic_output_path = os.path.join(output_dir, WORDTOPIC_FILENAME)
    check_remove(wordtopic_output_path, logger, force)
    create_directory(output_dir, logger)

    logger.info("Loading vocabulary ...")
    with open(words_input_path, "r", encoding="utf-8") as fin:
        word_index: Dict[int, str] = {
            i: word.replace("\n", "") for i, word in enumerate(fin)
        }

    logger.info("Creating corpus ...")
    corpus = create_gensim_corpus(docword_input_path, logger)
    if consolidate:
        logger.info("Creating training corpus ...")
        training_corpus = create_gensim_corpus(docword_concat_input_path, logger)
    else:
        training_corpus = corpus

    logger.info("Training HDP model ...")
    hdp = gensim.models.HdpModel(
        training_corpus,
        gensim.corpora.Dictionary.from_corpus(training_corpus, word_index),
        chunksize=chunk_size,
        kappa=kappa,
        tau=tau,
        K=K,
        T=T,
        alpha=alpha,
        gamma=gamma,
        eta=eta,
        scale=scale,
        var_converge=var_converge,
    )
    logger.info("Trained the model.")

    logger.info("Inferring topics per document ...")
    document_topics = np.empty((len(corpus), T))
    for ind_doc, bow in tqdm.tqdm(enumerate(corpus)):
        gammas = hdp.inference([bow])[0]
        document_topics[ind_doc, :] = gammas / sum(gammas)

    logger.info("Saving topics per document ...")
    np.save(doctopic_output_path, document_topics)
    logger.info("Saved topics per document in '%s'." % doctopic_output_path)

    logger.info("Saving word/topic distribution ...")
    np.save(wordtopic_output_path, hdp.get_topics())
    logger.info("Saved word/topic distribution in '%s'." % wordtopic_output_path)
