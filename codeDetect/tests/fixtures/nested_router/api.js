const express = require('express');
const api = express.Router();
const projects = require('./projects');
api.use('/projects', projects);
module.exports = api;
